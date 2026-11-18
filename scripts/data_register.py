#!/usr/bin/env python3
"""Track every dataset used by the research repo so it can be restored.

``docs/data_register.json`` is the source of truth.  ``data/`` is gitignored, so
the register lives under ``docs/`` and records where each dataset came from,
when it was retrieved, how big it was, and the exact command that re-downloads
or rebuilds it.

Commands::

    python scripts/data_register.py list
    python scripts/data_register.py verify
    python scripts/data_register.py render
    python scripts/data_register.py add --name ... --path ... --source ...

``verify`` is the post-restore check: it proves every registered path is present
and within 10% of its recorded size.  Only the standard library is used.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER_PATH = REPO_ROOT / "docs" / "data_register.json"
MARKDOWN_PATH = REPO_ROOT / "docs" / "DATA_REGISTER.md"

SIZE_TOLERANCE = 0.10
REQUIRED_KEYS = (
    "name",
    "kind",
    "path",
    "source",
    "source_url",
    "retrieved",
    "size_bytes",
    "license",
    "rebuild",
    "notes",
)
VALID_KINDS = ("raw", "derived")


# ---------------------------------------------------------------------------
# register I/O


def load_register(path: Path = REGISTER_PATH) -> list[dict[str, Any]]:
    """Read the JSON register and return its entries (preserving file order)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array of entries")
    return data


def save_register(entries: Iterable[dict[str, Any]], path: Path = REGISTER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(list(entries), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# measurement / verification


def measure_path(path: Path) -> int | None:
    """Return a file's size or a directory's recursive size, None if missing."""
    if not path.exists():
        return None
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def _size_status(expected: int, actual: int) -> str:
    if expected == actual:
        return "OK"
    if expected <= 0:
        return "SIZE_DRIFT"
    if abs(actual - expected) / expected <= SIZE_TOLERANCE:
        return "OK"
    return "SIZE_DRIFT"


def verify_entries(
    entries: Iterable[dict[str, Any]], root: Path = REPO_ROOT
) -> list[dict[str, Any]]:
    """Check existence and size for each entry without mutating anything."""
    results: list[dict[str, Any]] = []
    for entry in entries:
        expected = int(entry.get("size_bytes") or 0)
        target = root / str(entry["path"])
        actual = measure_path(target)
        if actual is None:
            status = "MISSING"
        else:
            status = _size_status(expected, actual)
        results.append(
            {
                "name": entry["name"],
                "path": str(entry["path"]),
                "status": status,
                "expected": expected,
                "actual": actual,
            }
        )
    return results


def format_bytes(size: int | None) -> str:
    if size is None:
        return "-"
    if size < 1000:
        return f"{size} B"
    units = ("KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        value /= 1000.0
        if value < 1000.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
    return f"{size} B"


# ---------------------------------------------------------------------------
# rendering


def _md(value: Any) -> str:
    """Make a value safe inside a markdown table cell."""
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _entry_summary(entry: dict[str, Any]) -> str:
    source = entry.get("source") or "unknown"
    url = entry.get("source_url")
    return f"{source} <{url}>" if url else source


def render_markdown(entries: Iterable[dict[str, Any]]) -> str:
    """Render the human-readable register.  Output is deterministic."""
    entries = list(entries)
    raw_count = sum(1 for e in entries if e.get("kind") == "raw")
    derived_count = sum(1 for e in entries if e.get("kind") == "derived")
    total_bytes = sum(int(e.get("size_bytes") or 0) for e in entries)

    lines: list[str] = [
        "# Data register",
        "",
        "This file is generated from [`docs/data_register.json`](data_register.json). "
        "Do not edit it by hand: edit the JSON (or use "
        "`python scripts/data_register.py add ...`) and then run "
        "`python scripts/data_register.py render`.",
        "",
        "**Add new data to `docs/data_register.json` before using it; include name, "
        "path, source URL, retrieval date, size, license and the exact "
        "re-download/rebuild command. Run `python scripts/data_register.py verify` "
        "after a restore.**",
        "",
        "`data/` is gitignored, so a clone contains no datasets. Every path below "
        "must be restored from its source or rebuilt from the recorded command. "
        "`verify` reports `MISSING` when a path is absent and `SIZE_DRIFT` when the "
        "measured size differs from the registered size by more than 10%.",
        "",
        f"Registered: **{len(entries)} datasets** "
        f"({raw_count} raw, {derived_count} derived), "
        f"**{format_bytes(total_bytes)}** on disk at registration.",
        "",
        "## Datasets",
        "",
        "| Name | Kind | Path | Size | Source | Retrieved | License |",
        "|---|---|---|---:|---|---|---|",
    ]
    for entry in entries:
        lines.append(
            "| `{name}` | {kind} | `{path}` | {size} | {source} | {retrieved} | {license} |".format(
                name=_md(entry.get("name")),
                kind=_md(entry.get("kind")),
                path=_md(entry.get("path")),
                size=_md(format_bytes(entry.get("size_bytes"))),
                source=_md(entry.get("source")),
                retrieved=_md(entry.get("retrieved") or "unknown"),
                license=_md(entry.get("license")),
            )
        )

    lines += ["", "## Re-download and rebuild notes", ""]
    for entry in entries:
        lines.append(f"### `{entry.get('name')}`")
        lines.append("")
        lines.append(f"- **Path:** `{entry.get('path')}`")
        lines.append(f"- **Kind:** {entry.get('kind')}")
        lines.append(f"- **Source:** {_entry_summary(entry)}")
        lines.append(f"- **Retrieved:** {entry.get('retrieved') or 'unknown (not recorded)'}")
        lines.append(
            f"- **Size:** {format_bytes(entry.get('size_bytes'))} "
            f"({int(entry.get('size_bytes') or 0):,} bytes)"
        )
        lines.append(f"- **License:** {entry.get('license') or 'unknown'}")
        rebuild = entry.get("rebuild")
        if rebuild:
            lines.append("- **Re-download / rebuild:**")
            lines.append("")
            lines.append("  ```bash")
            for command in str(rebuild).splitlines():
                lines.append(f"  {command}")
            lines.append("  ```")
        else:
            lines.append("- **Re-download / rebuild:** not recorded")
        notes = entry.get("notes")
        if notes:
            lines.append(f"- **Notes:** {notes}")
        lines.append("")

    lines += [
        "---",
        "",
        "Generated by `scripts/data_register.py render`. "
        "Run `python scripts/data_register.py verify` after a restore.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# add


def add_entry(
    name: str,
    path: str,
    source: str,
    *,
    source_url: str | None = None,
    kind: str = "raw",
    rebuild: str | None = None,
    notes: str = "",
    license: str = "unknown",
    retrieved: str | None = None,
    size_bytes: int | None = None,
    root: Path = REPO_ROOT,
    register_path: Path = REGISTER_PATH,
) -> dict[str, Any]:
    """Append one entry, refusing a duplicate name.  Returns the new entry."""
    if not name.strip():
        raise ValueError("name must not be empty")
    if not str(path).strip():
        raise ValueError("path must not be empty")
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")

    entries = load_register(register_path)
    if any(entry.get("name") == name for entry in entries):
        raise ValueError(f"an entry named {name!r} already exists")

    normalised_path = str(path).replace("\\", "/")
    while normalised_path.startswith("./"):
        normalised_path = normalised_path[2:]
    if size_bytes is None:
        size_bytes = measure_path(root / normalised_path) or 0

    entry = {
        "name": name,
        "kind": kind,
        "path": normalised_path,
        "source": source,
        "source_url": source_url,
        "retrieved": retrieved,
        "size_bytes": int(size_bytes),
        "license": license,
        "rebuild": rebuild,
        "notes": notes,
    }
    entries.append(entry)
    save_register(entries, register_path)
    return entry


# ---------------------------------------------------------------------------
# CLI


def cmd_list(entries: list[dict[str, Any]], _args: argparse.Namespace) -> int:
    rows = [
        (
            entry["name"],
            entry["kind"],
            entry["path"],
            format_bytes(entry.get("size_bytes")),
            entry.get("source") or "",
        )
        for entry in entries
    ]
    headers = ("NAME", "KIND", "PATH", "SIZE", "SOURCE")
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    print(f"\n{len(rows)} entries")
    return 0


def cmd_verify(entries: list[dict[str, Any]], _args: argparse.Namespace) -> int:
    results = verify_entries(entries)
    name_width = max((len(item["name"]) for item in results), default=4)
    missing = 0
    drifted = 0
    for item in results:
        if item["status"] == "MISSING":
            missing += 1
            detail = f"expected {int(item['expected']):,} bytes"
        elif item["status"] == "SIZE_DRIFT":
            drifted += 1
            detail = f"expected {int(item['expected']):,}, actual {int(item['actual']):,} bytes"
        else:
            detail = f"{int(item['actual']):,} bytes"
        print(f"{item['status']:<10} {item['name']:<{name_width}}  {item['path']}  ({detail})")
    ok = len(results) - missing - drifted
    print(f"\n{len(results)} entries: {ok} OK, {missing} missing, {drifted} size drift")
    if missing:
        print("FAIL: restore the missing paths before using the data.")
        return 1
    return 0


def cmd_render(entries: list[dict[str, Any]], args: argparse.Namespace) -> int:
    output = Path(args.output) if args.output else MARKDOWN_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(entries), encoding="utf-8")
    print(f"wrote {output} ({len(entries)} entries)")
    return 0


def cmd_add(entries: list[dict[str, Any]], args: argparse.Namespace) -> int:
    try:
        entry = add_entry(
            args.name,
            args.path,
            args.source,
            source_url=args.source_url,
            kind=args.kind,
            rebuild=args.rebuild,
            notes=args.notes,
            license=args.license,
            retrieved=args.retrieved,
            size_bytes=args.size_bytes,
            register_path=Path(args.register),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"added {entry['name']} ({entry['kind']}, {format_bytes(entry['size_bytes'])}) "
        f"to {args.register}"
    )
    if entry["size_bytes"] == 0:
        print("warning: path is absent or empty; sizes will drift until it is restored")
    print("next: run `python scripts/data_register.py render` to update docs/DATA_REGISTER.md")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data_register",
        description="Track datasets so they can be re-downloaded or rebuilt after a restore.",
    )
    parser.add_argument(
        "--register",
        default=str(REGISTER_PATH),
        help="path to the JSON register (default: docs/data_register.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="table of registered datasets").set_defaults(func=cmd_list)

    sub.add_parser("verify", help="check paths exist and sizes are within 10%%").set_defaults(
        func=cmd_verify
    )

    render = sub.add_parser("render", help="regenerate docs/DATA_REGISTER.md")
    render.add_argument(
        "--output",
        default=None,
        help="write somewhere other than docs/DATA_REGISTER.md",
    )
    render.set_defaults(func=cmd_render)

    add = sub.add_parser("add", help="append a new dataset entry")
    add.add_argument("--name", required=True)
    add.add_argument("--path", required=True, help="path relative to the repo root")
    add.add_argument("--source", required=True, help="human-readable source description")
    add.add_argument("--source-url", default=None)
    add.add_argument("--kind", choices=VALID_KINDS, default="raw")
    add.add_argument("--rebuild", default=None, help="exact re-download/rebuild command")
    add.add_argument("--notes", default="")
    add.add_argument("--license", default="unknown")
    add.add_argument(
        "--retrieved",
        default=date.today().isoformat(),
        help="YYYY-MM-DD retrieval date (default: today); pass 'null' if unknown",
    )
    add.add_argument(
        "--size-bytes",
        type=int,
        default=None,
        help="override the measured size (default: measure the path now)",
    )
    add.set_defaults(func=cmd_add)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    register_path = Path(args.register)
    try:
        entries = load_register(register_path)
    except FileNotFoundError:
        print(f"error: register not found: {register_path}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"error: invalid register {register_path}: {exc}", file=sys.stderr)
        return 2
    if args.command == "add" and args.retrieved == "null":
        args.retrieved = None
    return int(args.func(entries, args))


if __name__ == "__main__":
    raise SystemExit(main())
