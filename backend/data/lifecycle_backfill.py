"""Resumable ESPN scoreboard backfill for pre-2026 match lifecycles.

The 2026 ESPN summary cache is the primary source.  For fixtures the cache does
not cover, this module discovers the ESPN event through the public scoreboard
endpoint and reads the same ``keyEvents`` payload as the offline builder.

Nothing here invents a timestamp: a fixture with no unique ESPN event, or an
event missing either ``kickoff`` or ``end-regular-time`` wallclock, produces no
row.  Unknown stays unknown.

All fetched payloads are cached gzipped under ``var/raw/espn`` so a failed or
interrupted run can resume without repeating successful requests.  The cache
is keyed by league/date for scoreboards and league/event id for summaries.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import duckdb

from backend import config
from backend.data import sources
from backend.data.lifecycle import extract_wallclocks
from backend.data.timeutil import as_utc, parse_iso_utc

# Polymarket's league codes map to ESPN's competition slugs.
ESPN_LEAGUE_SLUGS: dict[str, str] = {
    "epl": "eng.1",
    "lal": "esp.1",
    "bun": "ger.1",
    "fl1": "fra.1",
}

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={dates}"
SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={event_id}"
USER_AGENT = "HermitTradingFrontend/0.1 (personal research; lifecycle backfill)"

DEFAULT_DELAY_S = 0.25
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_S = 0.5
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Club-name decoration that carries no identity.  Both a leading and trailing
# occurrence are removed as extra aliases; exact matching still happens on the
# remaining normalised name, so this is deterministic rather than fuzzy.
_CLUB_TOKENS = frozenset(
    {"fc", "afc", "cf", "ac", "as", "ss", "ssc", "rc", "cd", "ud", "sv", "sc", "club"}
)


def normalize_team(value: object | None) -> str:
    """Lowercase ASCII team name with punctuation and runs of space removed."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _name_variants(value: object | None) -> set[str]:
    base = normalize_team(value)
    if not base:
        return set()
    variants = {base}
    tokens = base.split()
    if len(tokens) > 1 and tokens[0] in _CLUB_TOKENS:
        variants.add(" ".join(tokens[1:]))
    if len(tokens) > 1 and tokens[-1] in _CLUB_TOKENS:
        variants.add(" ".join(tokens[:-1]))
    return variants


def _fixture_aliases(
    name: object | None, norm: object | None, code: object | None
) -> frozenset[str]:
    aliases: set[str] = set()
    aliases.update(_name_variants(norm))
    aliases.update(_name_variants(name))
    normalised_code = normalize_team(code)
    if normalised_code:
        aliases.add(normalised_code)
    return frozenset(aliases)


def _team_aliases(team: Mapping[str, Any]) -> frozenset[str]:
    aliases: set[str] = set()
    for key in ("displayName", "shortDisplayName", "name", "location"):
        aliases.update(_name_variants(team.get(key)))
    abbreviation = normalize_team(team.get("abbreviation"))
    if abbreviation:
        aliases.add(abbreviation)
    return frozenset(aliases)


@dataclass(frozen=True)
class Fixture:
    match_key: str
    league: str
    event_date: date
    home_aliases: frozenset[str]
    away_aliases: frozenset[str]


@dataclass(frozen=True)
class EspnEvent:
    event_id: str
    event_date: date
    home_aliases: frozenset[str]
    away_aliases: frozenset[str]


@dataclass
class LifecycleBackfillReport:
    """Counters for one backfill run; every value is derived, never estimated."""

    dates_scanned: int = 0
    requests: int = 0
    cache_hits: int = 0
    matched: int = 0
    unmatched: int = 0
    incomplete: int = 0
    skipped: int = 0
    rows_written: int = 0
    fixtures: int = 0
    preserved_existing: int = 0
    errors: list[str] = field(default_factory=list)
    leagues: list[str] = field(default_factory=list)
    from_date: str | None = None
    to_date: str | None = None
    built_on: str = ""
    lifecycle_version: str | None = None
    lifecycle_version_date: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dates_scanned": self.dates_scanned,
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "incomplete": self.incomplete,
            "skipped": self.skipped,
            "rows_written": self.rows_written,
            "fixtures": self.fixtures,
            "preserved_existing": self.preserved_existing,
            "errors": list(self.errors),
            "leagues": list(self.leagues),
            "from_date": self.from_date,
            "to_date": self.to_date,
            "built_on": self.built_on,
            "lifecycle_version": self.lifecycle_version,
            "lifecycle_version_date": self.lifecycle_version_date,
        }


@dataclass
class BackfillResult:
    rows: list[dict[str, Any]]
    report: LifecycleBackfillReport
    input_files: list[Path]


class HttpError(RuntimeError):
    """A non-retryable HTTP failure, or a retryable one that ran out of tries."""

    def __init__(self, url: str, status: int | None, detail: str = "") -> None:
        self.url = url
        self.status = status
        suffix = f" ({detail})" if detail else ""
        code = f"HTTP {status}" if status is not None else "network error"
        super().__init__(f"{code} for {url}{suffix}")


def fetch_json(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urllib.request.urlopen,
    user_agent: str = USER_AGENT,
) -> dict[str, Any]:
    """GET ``url`` and return a JSON object, retrying only transient failures.

    A mocked ``opener`` only has to return an object with ``read()`` and an
    optional ``status``; the tests use that seam and never touch the network.
    """
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    attempts = max(0, int(max_retries)) + 1
    last_detail = ""
    for attempt in range(attempts):
        status: int | None = None
        body = b""
        try:
            response = opener(request, timeout=timeout_s)
            try:
                status = int(getattr(response, "status", 200) or 200)
                body = response.read()
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            body = exc.read() if hasattr(exc, "read") else b""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            status = None
            last_detail = str(exc)

        if status is not None and 200 <= status < 300:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise HttpError(url, status, f"invalid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise HttpError(url, status, "JSON payload is not an object")
            return payload

        if status is not None and status not in RETRYABLE_STATUSES:
            raise HttpError(url, status)

        if attempt + 1 < attempts:
            sleep(retry_backoff_s * (2**attempt))
            continue
        if status is None:
            raise HttpError(url, None, last_detail)
        raise HttpError(url, status)
    raise HttpError(url, None, "exhausted retries")  # pragma: no cover - loop returns


def _default_scoreboard_dir() -> Path:
    return Path(config.VAR_DIR) / "raw/espn/scoreboard"


def _default_summary_dir() -> Path:
    return Path(config.VAR_DIR) / "raw/espn/summary"


def _read_gzip_json(path: Path) -> dict[str, Any] | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, EOFError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_gzip_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)
    return path


def _as_date(value: object | None) -> date | None:
    if isinstance(value, datetime):
        return as_utc(value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return parse_iso_utc(value).date()
        except ValueError:
            return None
    return None


def _event_fixtures(events_path: Path, leagues: Iterable[str]) -> list[Fixture]:
    """Read the Polymarket match spine, tolerating older fixture schemas."""
    con = duckdb.connect()
    try:
        cursor = con.execute("SELECT * FROM read_parquet(?)", [str(events_path)])
        columns = [item[0] for item in cursor.description]
        raw_rows = cursor.fetchall()
    finally:
        con.close()

    wanted = set(leagues)
    fixtures: list[Fixture] = []
    seen: set[str] = set()
    for values in raw_rows:
        row = dict(zip(columns, values))
        match_key = row.get("match_key")
        league = row.get("league")
        event_date = _as_date(row.get("event_date"))
        if not match_key or not league or event_date is None:
            continue
        league = str(league).lower()
        if league not in wanted:
            continue
        match_key = str(match_key)
        if match_key in seen:
            continue
        seen.add(match_key)
        fixtures.append(
            Fixture(
                match_key=match_key,
                league=league,
                event_date=event_date,
                home_aliases=_fixture_aliases(
                    row.get("home_name"), row.get("home_norm"), row.get("home_code")
                ),
                away_aliases=_fixture_aliases(
                    row.get("away_name"), row.get("away_norm"), row.get("away_code")
                ),
            )
        )
    return sorted(fixtures, key=lambda item: (item.event_date, item.league, item.match_key))


def _parse_scoreboard(payload: Mapping[str, Any]) -> list[EspnEvent]:
    events: list[EspnEvent] = []
    seen: set[str] = set()
    for raw in payload.get("events") or ():
        if not isinstance(raw, dict):
            continue
        event_id = raw.get("id")
        if event_id is None:
            continue
        event_id = str(event_id)
        if event_id in seen:
            continue
        event_date = _as_date(raw.get("date"))
        competitions = raw.get("competitions") or ()
        competition = next(
            (item for item in competitions if isinstance(item, dict)), None
        )
        if competition is None:
            continue
        if event_date is None:
            event_date = _as_date(competition.get("date"))
        if event_date is None:
            continue
        home: Mapping[str, Any] | None = None
        away: Mapping[str, Any] | None = None
        for competitor in competition.get("competitors") or ():
            if not isinstance(competitor, dict):
                continue
            side = str(competitor.get("homeAway") or "").lower()
            team = competitor.get("team")
            if not isinstance(team, dict):
                continue
            if side == "home":
                home = team
            elif side == "away":
                away = team
        if home is None or away is None:
            continue
        seen.add(event_id)
        events.append(
            EspnEvent(
                event_id=event_id,
                event_date=event_date,
                home_aliases=_team_aliases(home),
                away_aliases=_team_aliases(away),
            )
        )
    return events


def _matches_side(fixture_aliases: frozenset[str], event_aliases: frozenset[str]) -> bool:
    return bool(fixture_aliases and event_aliases and fixture_aliases & event_aliases)


def _unique_event(fixture: Fixture, events: Iterable[EspnEvent]) -> EspnEvent | None:
    candidates: dict[str, EspnEvent] = {}
    for event in events:
        if event.event_date != fixture.event_date:
            continue
        normal = _matches_side(fixture.home_aliases, event.home_aliases) and _matches_side(
            fixture.away_aliases, event.away_aliases
        )
        reversed_ = _matches_side(fixture.home_aliases, event.away_aliases) and _matches_side(
            fixture.away_aliases, event.home_aliases
        )
        if normal or reversed_:
            candidates[event.event_id] = event
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return None


class _Requester:
    """A polite, rate-limited wrapper around a JSON fetcher."""

    def __init__(
        self,
        fetcher: Callable[[str], dict[str, Any]],
        *,
        delay_s: float,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self.fetcher = fetcher
        self.delay_s = max(0.0, float(delay_s))
        self.sleep = sleep
        self.clock = clock
        self.last_request: float | None = None

    def get(self, url: str) -> dict[str, Any]:
        if self.last_request is not None and self.delay_s > 0:
            elapsed = self.clock() - self.last_request
            remaining = self.delay_s - elapsed
            if remaining > 0:
                self.sleep(remaining)
        try:
            return self.fetcher(url)
        finally:
            self.last_request = self.clock()


def run_backfill(
    *,
    events_path: Path | None = None,
    scoreboard_dir: Path | None = None,
    summary_dir: Path | None = None,
    source_summary_dir: Path | None = None,
    skip_match_keys: Iterable[str] = (),
    from_date: date | None = None,
    to_date: date | None = None,
    leagues: Iterable[str] | None = None,
    limit: int | None = None,
    delay_s: float = DEFAULT_DELAY_S,
    refresh: bool = False,
    fetcher: Callable[[str], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    today: date | None = None,
) -> BackfillResult:
    """Fetch missing pre-2026 lifecycles and return rows plus counters.

    ``skip_match_keys`` are fixtures already covered by an observed cached
    summary or by an existing lifecycle artifact; they are never re-fetched.
    """
    events = Path(events_path) if events_path is not None else sources.require_file("polymarket_events")
    scoreboard_root = Path(scoreboard_dir) if scoreboard_dir is not None else _default_scoreboard_dir()
    summary_root = Path(summary_dir) if summary_dir is not None else _default_summary_dir()
    source_root = Path(source_summary_dir) if source_summary_dir is not None else Path(config.ESPN_SUMMARY_DIR)

    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    if from_date is not None and to_date is not None and from_date > to_date:
        raise ValueError("from_date must be <= to_date")

    selected = _normalise_leagues(leagues)
    report = LifecycleBackfillReport(
        leagues=selected,
        from_date=from_date.isoformat() if from_date else None,
        to_date=to_date.isoformat() if to_date else None,
        built_on=(today or date.today()).isoformat(),
    )

    fixtures = _event_fixtures(events, selected)
    if from_date is not None:
        fixtures = [item for item in fixtures if item.event_date >= from_date]
    if to_date is not None:
        fixtures = [item for item in fixtures if item.event_date <= to_date]

    protected = {str(key) for key in skip_match_keys}
    candidates = [item for item in fixtures if item.match_key not in protected]
    report.skipped = len(fixtures) - len(candidates)
    report.fixtures = len(candidates)
    if limit is not None:
        candidates = candidates[:limit]
        report.fixtures = len(candidates)

    default_fetcher = fetcher
    if default_fetcher is None:

        def default_fetcher(url: str) -> dict[str, Any]:
            return fetch_json(
                url,
                timeout_s=timeout_s,
                max_retries=max_retries,
                retry_backoff_s=retry_backoff_s,
                sleep=sleep,
            )

    requester = _Requester(default_fetcher, delay_s=delay_s, sleep=sleep, clock=clock)

    grouped: dict[tuple[str, date], list[Fixture]] = {}
    for fixture in candidates:
        grouped.setdefault((fixture.league, fixture.event_date), []).append(fixture)

    rows: list[dict[str, Any]] = []
    input_files: list[Path] = []
    for (league_code, event_date), group in sorted(grouped.items()):
        report.dates_scanned += 1
        espn_league = ESPN_LEAGUE_SLUGS[league_code]
        scoreboard_path = scoreboard_root / f"{espn_league}_{event_date.isoformat()}.json.gz"
        scoreboard = _read_gzip_json(scoreboard_path) if not refresh else None
        if scoreboard is not None:
            report.cache_hits += 1
        else:
            try:
                scoreboard = requester.get(
                    SCOREBOARD_URL.format(
                        league=espn_league, dates=event_date.strftime("%Y%m%d")
                    )
                )
                report.requests += 1
                _write_gzip_json(scoreboard_path, scoreboard)
            except HttpError as exc:
                report.errors.append(f"scoreboard {league_code} {event_date}: {exc}")
                continue
        input_files.append(scoreboard_path)
        events_for_date = _parse_scoreboard(scoreboard)

        for fixture in group:
            event = _unique_event(fixture, events_for_date)
            if event is None:
                report.unmatched += 1
                continue
            report.matched += 1
            payload, summary_path = _load_summary(
                league_code,
                espn_league,
                event.event_id,
                source_root=source_root,
                cache_root=summary_root,
                refresh=refresh,
                requester=requester,
                report=report,
            )
            if payload is None:
                report.errors.append(
                    f"summary {league_code} event {event.event_id}: fetch failed"
                )
                continue
            input_files.append(summary_path)
            kickoff, end = extract_wallclocks(payload)
            if kickoff is None or end is None:
                report.incomplete += 1
                continue
            rows.append(
                {
                    "match_key": fixture.match_key,
                    "actual_kickoff_utc": kickoff,
                    "actual_end_utc": end,
                    "source": "espn_keyevents",
                    "precision": "observed",
                }
            )
            report.rows_written += 1

    rows.sort(key=lambda item: item["match_key"])
    # Deterministic input order and no duplicate fingerprints.
    unique_inputs = sorted({path for path in input_files}, key=str)
    return BackfillResult(rows=rows, report=report, input_files=unique_inputs)


def _normalise_leagues(leagues: Iterable[str] | None) -> list[str]:
    if leagues is None:
        return list(ESPN_LEAGUE_SLUGS)
    selected: list[str] = []
    for raw in leagues:
        value = str(raw).strip().lower()
        if not value:
            continue
        if value not in ESPN_LEAGUE_SLUGS:
            raise ValueError(
                f"unknown league '{raw}'; expected one of {', '.join(ESPN_LEAGUE_SLUGS)}"
            )
        if value not in selected:
            selected.append(value)
    return selected or list(ESPN_LEAGUE_SLUGS)


def _load_summary(
    league_code: str,
    espn_league: str,
    event_id: str,
    *,
    source_root: Path,
    cache_root: Path,
    refresh: bool,
    requester: _Requester,
    report: LifecycleBackfillReport,
) -> tuple[dict[str, Any] | None, Path]:
    # The 2026 source cache uses Polymarket league codes; the backfill cache
    # uses ESPN competition slugs, matching the scoreboard cache.
    source_path = source_root / f"{league_code}_{event_id}.json.gz"
    cache_path = cache_root / f"{espn_league}_{event_id}.json.gz"
    if not refresh:
        for path in (source_path, cache_path):
            payload = _read_gzip_json(path)
            if payload is not None:
                report.cache_hits += 1
                return payload, path
    try:
        payload = requester.get(
            SUMMARY_URL.format(league=espn_league, event_id=event_id)
        )
    except HttpError:
        return None, cache_path
    report.requests += 1
    _write_gzip_json(cache_path, payload)
    return payload, cache_path
