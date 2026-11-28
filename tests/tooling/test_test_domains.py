from __future__ import annotations

import os
import subprocess
from pathlib import Path

from backend.config import ROOT


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(Path(ROOT) / "scripts" / "test_domains.sh"), *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_unknown_domain_is_rejected():
    result = _run("--domain", "not-a-domain")

    assert result.returncode == 2
    assert "unknown domain: not-a-domain" in result.stderr


def test_unconfigured_e2e_is_reported_as_blocked_not_passed():
    env = os.environ.copy()
    env.pop("HTF_E2E", None)
    result = _run("--domain", "e2e", env=env)

    assert result.returncode == 2
    assert "BLOCKED: set HTF_E2E=1" in result.stdout
    assert "blocked=e2e" in result.stdout
    assert "passed=none" in result.stdout
