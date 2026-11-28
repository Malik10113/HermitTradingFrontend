from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend import config
from backend.data import bars


def _load_script(name: str):
    path = Path(config.ROOT) / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"htf_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_bars_cli_writes_requested_alias(dataset, capsys):
    script = _load_script("build_bars")
    code = script.main(["--instrument", dataset.yes_id, "--alias", "1m"])
    assert code == 0
    output = capsys.readouterr().out
    assert "data_version=sha256:" in output
    path = Path(config.BARS_DIR) / dataset.yes_id / "1m.parquet"
    assert path.exists()


def test_build_catalog_cli_is_idempotent_and_uses_instrument_precision(dataset):
    from nautilus_trader.persistence import ParquetDataCatalog

    script = _load_script("build_catalog")
    args = ["--instrument", dataset.yes_id, "--alias", "1m"]
    assert script.main(args) == 0
    catalog_dir = Path(config.CATALOG_DIR)
    assert catalog_dir.is_dir()
    catalog = ParquetDataCatalog(str(catalog_dir))
    identifiers = [f"{dataset.yes_id}-1-MINUTE-LAST-EXTERNAL"]
    first = catalog.query_bars(identifiers=identifiers)
    assert len(first) == 3
    assert first[0].volume.precision == 2  # instrument.make_qty, not from_int
    files_before = sorted(catalog_dir.rglob("*.parquet"))

    assert script.main(args) == 0
    second = ParquetDataCatalog(str(catalog_dir)).query_bars(identifiers=identifiers)
    assert len(second) == 3
    assert sorted(catalog_dir.rglob("*.parquet")) == files_before


def test_failed_catalog_promotion_preserves_the_previous_catalog(dataset, monkeypatch):
    script = _load_script("build_catalog")
    args = ["--instrument", dataset.yes_id, "--alias", "1m"]
    assert script.main(args) == 0

    catalog_dir = Path(config.CATALOG_DIR)
    files_before = sorted(path.relative_to(catalog_dir) for path in catalog_dir.rglob("*"))
    real_replace = script.os.replace
    temp = catalog_dir.with_name(f".{catalog_dir.name}.tmp-{script.os.getpid()}")

    def fail_new_catalog(source, destination):
        if Path(source) == temp and Path(destination) == catalog_dir:
            raise OSError("simulated promotion failure")
        return real_replace(source, destination)

    monkeypatch.setattr(script.os, "replace", fail_new_catalog)
    with pytest.raises(OSError, match="simulated promotion failure"):
        script.main(args)

    assert catalog_dir.is_dir()
    assert sorted(path.relative_to(catalog_dir) for path in catalog_dir.rglob("*")) == files_before
    assert not temp.exists()
