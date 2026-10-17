"""Package the native extension with the libraries and notices it requires."""

import argparse
import hashlib
import tarfile
from pathlib import Path

from audit_dependencies import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--duckdb-version", required=True)
    parser.add_argument("--build-dir", default="build")
    args = parser.parse_args()
    root = Path(args.build_dir) / "extension/ortools"
    if not (root / "ortools.duckdb_extension").exists() or not (root / "lib").is_dir():
        raise SystemExit("Build the extension and its lib directory first")
    print(audit(root))
    target = Path("dist") / f"ortools-v0.1.0-duckdb-v{args.duckdb_version}-{args.platform}.tar.gz"
    target.parent.mkdir(exist_ok=True)
    with tarfile.open(target, "w:gz") as package:
        package.add(root / "ortools.duckdb_extension", arcname="ortools/ortools.duckdb_extension")
        package.add(root / "lib", arcname="ortools/lib")
        package.add(root / "build-receipt.json", arcname="ortools/build-receipt.json")
        for name in ("README.md", "LICENSE", "NOTICE"):
            package.add(name, arcname="ortools/" + name)
        package.add("src/include/LICENSE.nlohmann-json", arcname="ortools/src/include/LICENSE.nlohmann-json")
        package.add("third_party/LICENSE.duckdb", arcname="ortools/third_party/LICENSE.duckdb")
        for directory in ("docs", "examples"):
            for source in Path(directory).iterdir():
                if source.is_file():
                    package.add(source, arcname="ortools/" + str(source))
        upstream = Path("vendor/ortools-minimal/share/licenses")
        if not upstream.is_dir():
            raise SystemExit("Missing source dependency license notices")
        package.add(upstream, arcname="ortools/upstream-notices")
        package.add(root / "duckdb-notices", arcname="ortools/upstream-notices/duckdb")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(f"{digest}  {target.name}\n")
    print(target)


if __name__ == "__main__":
    main()
