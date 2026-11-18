"""Registered read-only input datasets.

Every dataset the dashboard reads is registered here with the configuration
attribute that resolves its path.  A missing source fails loudly and names the
dataset plus the register entry that documents it; there is no silent fallback.

The sibling data root is opened read-only.  This module never writes to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend import config


@dataclass(frozen=True)
class Dataset:
    """One registered read-only source."""

    name: str
    config_attr: str
    timestamp_column: str | None
    description: str

    @property
    def path(self) -> Path:
        return Path(getattr(config, self.config_attr))

    @property
    def exists(self) -> bool:
        return self.path.is_file()


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        "polymarket_epl_tape",
        "EPL_TAPE",
        "timestamp",
        "Polymarket EPL moneyline fill tape (YES-normalised)",
    ),
    Dataset(
        "polymarket_football_tape",
        "FOOTBALL_TAPE",
        "timestamp",
        "Polymarket Big-4 moneyline fill tape (YES-normalised)",
    ),
    Dataset(
        "polymarket_markets",
        "MARKETS",
        None,
        "Polymarket SII market metadata and YES/NO token ids",
    ),
    Dataset(
        "polymarket_events",
        "POLYMARKET_EVENTS",
        None,
        "Polymarket match spine (home/away names, league, date)",
    ),
    Dataset(
        "polymarket_results_markets",
        "POLYMARKET_MARKETS",
        None,
        "Polymarket moneyline results with settlement values",
    ),
    Dataset(
        "espn_fixture_map",
        "FIXTURE_MAP",
        None,
        "Fixture to ESPN event id map used by the lifecycle builder",
    ),
    Dataset(
        "espn_summary_dir",
        "ESPN_SUMMARY_DIR",
        None,
        "Cached ESPN summary JSON (keyEvents kickoff / end-regular-time)",
    ),
)

_BY_NAME = {dataset.name: dataset for dataset in DATASETS}


class MissingDatasetError(FileNotFoundError):
    """A registered dataset is absent; the message names the register entry."""

    def __init__(self, dataset: str, path: Path, detail: str = "") -> None:
        self.dataset = dataset
        self.path = path
        suffix = f" {detail}" if detail else ""
        super().__init__(
            f"dataset '{dataset}' is missing at {path}.{suffix} "
            "This project reads datasets read-only; restore it from the source "
            "recorded in docs/DATA_REGISTER.md and re-run the recorded rebuild "
            "command. Refusing to fall back to a different source."
        )


def get_dataset(name: str) -> Dataset:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"unknown dataset '{name}'") from exc


def dataset_path(name: str) -> Path:
    return get_dataset(name).path


def require_dataset(name: str) -> Path:
    """Return the dataset path or raise :class:`MissingDatasetError`."""
    dataset = get_dataset(name)
    path = dataset.path
    if not path.exists():
        raise MissingDatasetError(name, path)
    return path


def require_file(name: str) -> Path:
    """Like :func:`require_dataset` but the source must be a regular file."""
    dataset = get_dataset(name)
    path = require_dataset(name)
    if not path.is_file():
        raise MissingDatasetError(name, path, "The path exists but is not a file.")
    return path


def require_dir(name: str) -> Path:
    dataset = get_dataset(name)
    path = require_dataset(name)
    if not path.is_dir():
        raise MissingDatasetError(name, path, "The path exists but is not a directory.")
    return path


def tape_datasets() -> list[Dataset]:
    return [get_dataset("polymarket_epl_tape"), get_dataset("polymarket_football_tape")]


def tape_paths(*, require: bool = True) -> list[Path]:
    """Existing tape files, EPL first; deterministic order."""
    out: list[Path] = []
    for dataset in tape_datasets():
        if dataset.exists:
            out.append(dataset.path)
        elif require:
            raise MissingDatasetError(dataset.name, dataset.path)
    return out


def relative_to_root(path: Path) -> str:
    """Stable provenance string relative to the read-only data root."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(config.DATA_ROOT.resolve()))
    except ValueError:
        return str(resolved)


def dataset_name_for(path: Path) -> str:
    """Name of the registered dataset a path belongs to, best effort."""
    resolved = Path(path).resolve()
    for dataset in DATASETS:
        try:
            if dataset.path.resolve() == resolved:
                return dataset.name
        except OSError:
            continue
    return "polymarket_moneyline_tape"


def path_from_relative(relative: str) -> Path:
    """Resolve a stored provenance string back to a read-only path."""
    candidate = Path(relative)
    if candidate.is_absolute():
        return candidate
    return Path(config.DATA_ROOT) / candidate


def status() -> dict[str, bool]:
    """Availability map used by ``/api/meta`` style diagnostics."""
    return {dataset.name: dataset.exists for dataset in DATASETS}
