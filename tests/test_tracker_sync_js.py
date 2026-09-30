"""Runs the Node unit tests for tracker-sync.js (merge logic + mocked Gist API)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_tracker_sync_js_suite():
    proc = subprocess.run(
        ["node", "--test", "tests/js"], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
