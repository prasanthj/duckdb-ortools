import os
from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def connection():
    binary = Path(os.environ.get("ORTOOLS_EXTENSION", "build/extension/ortools/ortools.duckdb_extension")).resolve()
    with duckdb.connect(config={"allow_unsigned_extensions": "true"}) as conn:
        conn.execute("LOAD '" + str(binary).replace("'", "''") + "'")
        yield conn
