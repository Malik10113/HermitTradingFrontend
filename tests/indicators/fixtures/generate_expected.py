"""Regenerate ``expected_series.json`` from the test reference implementations.

Run from the repository root::

    uv run python -m tests.indicators.fixtures.generate_expected

The JSON is checked in so a parity failure shows a diff against a frozen
expectation; it is produced by ``tests/indicators/reference.py`` (plain Python
from first principles), never by copying NautilusTrader output.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.indicators.fixtures.bars import make_bars
from tests.indicators.reference import reference_series

OUTPUT = Path(__file__).with_name("expected_series.json")

CASES: dict[str, dict[str, float]] = {
    "ema": {"period": 20},
    "rsi": {"period": 14},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
}


def build_payload() -> dict[str, object]:
    bars = make_bars()
    return {
        "bar_count": len(bars),
        "source": "tests/indicators/reference.py (first-principles definitions)",
        "indicators": {
            indicator_id: {
                "params": params,
                "series": reference_series(indicator_id, params, bars),
            }
            for indicator_id, params in CASES.items()
        },
    }


def main() -> None:
    OUTPUT.write_text(
        json.dumps(build_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
