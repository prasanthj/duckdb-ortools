"""Load a packaged extension in a fresh process outside its build directory."""

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from audit_dependencies import audit, audit_single_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--single-file", action="store_true", help="Verify a standalone community extension file")
    parser.add_argument("--backend-probe", required=True, help="Native solver-factory test executable from this build")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ortools-package-") as directory:
        if args.single_file:
            binary = Path(directory) / "ortools.duckdb_extension"
            shutil.copy2(args.archive, binary)
            print(audit_single_file(binary))
        else:
            with tarfile.open(args.archive) as package:
                package.extractall(directory, filter="data")
            binary = Path(directory) / "ortools/ortools.duckdb_extension"
            print(audit(binary.parent))
        environment = {
            key: value for key, value in os.environ.items() if key not in {"LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}
        }
        # Run the same factory regression against the extracted package's libs,
        # outside all build directories and without loader-path overrides.
        probe = binary.parent / "ortools_backend_test"
        shutil.copy2(args.backend_probe, probe)
        subprocess.run([str(probe)], cwd=directory, env=environment, check=True)
        subprocess.run(
            [
                sys.executable,
                "-c",
                """
import json, sys, duckdb
with duckdb.connect(config={'allow_unsigned_extensions': 'true'}) as connection:
    connection.execute("LOAD '" + sys.argv[1].replace("'", "''") + "'")
    for backend, upper in [('cp_sat', 3), ('glop', 3.75)]:
        model = {'version': 1, 'solver': backend, 'maximize': True,
                 'variables': [{'id': 'x', 'upper': upper, 'objective': 2}], 'constraints': []}
        result = connection.execute('SELECT ortools_solve(?)', [json.dumps(model)]).fetchone()[0]
        assert result['status'] == 'OPTIMAL' and result['objective'] == upper * 2, result
    print('Relocated native package verified:', connection.execute('SELECT version(), ortools_version()').fetchone())
    if sys.argv[2] == 'single-file':
        licenses = connection.execute('SELECT ortools_licenses()').fetchone()[0]
        assert 'Apache License' in licenses and 'Redistribution and use in source and binary forms' in licenses
""",
                str(binary),
                "single-file" if args.single_file else "archive",
            ],
            cwd=directory,
            env=environment,
            check=True,
        )


if __name__ == "__main__":
    main()
