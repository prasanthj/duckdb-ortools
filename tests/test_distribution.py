"""Exercise the actual linked solver factories, beyond SQL backend validation."""

import os
import subprocess
from pathlib import Path


def test_excluded_solver_factories_unavailable():
    extension = Path(os.environ.get("ORTOOLS_EXTENSION", "build/extension/ortools/ortools.duckdb_extension")).resolve()
    result = subprocess.run(
        [str(extension.parent / "ortools_backend_test")], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
