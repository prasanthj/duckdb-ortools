"""Reject non-permissive/unknown libraries and unresolved bundle dependencies."""

import argparse
import json
import platform
import re
import subprocess
from pathlib import Path

# This is the distribution policy, not a list inferred from the build under test.
BUNDLED = re.compile(
    r"lib(?:ortools|absl_[A-Za-z0-9_]+|protobuf(?:-lite)?|protoc|utf8_(?:range|validity)|re2|z|bz2)(?:[.-][0-9.]+)?\.(?:dylib|so(?:\.[0-9.]+)?)$"
)
SYSTEM_LINUX = {
    "libc.so.6",
    "libm.so.6",
    "libpthread.so.0",
    "librt.so.1",
    "libdl.so.2",
    "libstdc++.so.6",
    "libgcc_s.so.1",
    "ld-linux-x86-64.so.2",
    "ld-linux-aarch64.so.1",
    "libatomic.so.1",
}
PERMISSIVE = {"Apache-2.0", "BSD-3-Clause", "MIT", "Zlib", "bzip2-1.0.6"}


def linked_libraries(binary):
    if platform.system() == "Darwin":
        output = subprocess.check_output(["otool", "-l", str(binary)], text=True)
        dependencies = [
            re.search(r"\n\s+name (.*?) \(offset", command).group(1)
            for command in re.split(r"Load command \d+\n", output)
            if re.search(r"\bcmd LC_(?:LOAD(?:_WEAK|_UPWARD)?|REEXPORT|LAZY_LOAD)_DYLIB\b", command)
        ]
        paths = [
            re.search(r"\n\s+path (.*?) \(offset", command).group(1)
            for command in re.split(r"Load command \d+\n", output)
            if re.search(r"\bcmd LC_RPATH\b", command)
        ]
        return dependencies, paths
    output = subprocess.check_output(["readelf", "-d", str(binary)], text=True)
    return (
        re.findall(r"\(NEEDED\).*?\[(.*?)\]", output),
        [path for entry in re.findall(r"\((?:RUNPATH|RPATH)\).*?\[(.*?)\]", output) for path in entry.split(":")],
    )


def audit(root):
    root = Path(root).resolve()
    lib = root / "lib"
    receipt = json.loads((root / "build-receipt.json").read_text())
    if set(receipt["disabled_backends"]) != {"COINOR", "GLPK", "SCIP", "HIGHS", "PDLP", "CPLEX", "GUROBI", "XPRESS"}:
        raise ValueError("Bundle is not built with the required minimal backend policy")
    if any(
        entry["license"] not in PERMISSIVE
        or any(license not in PERMISSIVE for license in entry.get("additional_licenses", {}).values())
        for entry in receipt["dependencies"]
    ):
        raise ValueError("Non-permissive source dependency in the bundle")
    libraries = list(lib.iterdir())
    if not libraries or not any(p.name.startswith("libortools.") for p in libraries):
        raise ValueError("Missing native OR-Tools library")
    for library in libraries:
        if not BUNDLED.fullmatch(library.name):
            raise ValueError(f"Forbidden or unknown bundled library: {library.name}")
        if library.is_symlink() and library.resolve().parent != lib:
            raise ValueError(f"Library symlink escapes bundle: {library}")
    checked = []
    for binary in [root / "ortools.duckdb_extension", *(p for p in libraries if not p.is_symlink())]:
        dependencies, paths = linked_libraries(binary)
        allowed_paths = (
            {"@loader_path", "@loader_path/lib"}
            if platform.system() == "Darwin"
            else {"$ORIGIN", "$ORIGIN/lib", "${ORIGIN}", "${ORIGIN}/lib"}
        )
        if any(path.rstrip("/") not in allowed_paths for path in paths):
            raise ValueError(f"Non-relocatable library search path in {binary.name}: {paths}")
        for dependency in dependencies:
            if platform.system() == "Darwin" and dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if platform.system() != "Darwin" and dependency in SYSTEM_LINUX:
                continue
            name = Path(dependency).name
            if not BUNDLED.fullmatch(name) or not (lib / name).is_file():
                raise ValueError(f"Forbidden, unknown, or missing dependency: {binary.name} -> {dependency}")
            if platform.system() == "Darwin" and not dependency.startswith(("@rpath/", "@loader_path/")):
                raise ValueError(f"Non-relocatable dependency: {dependency}")
        checked.append(binary.name)
    return {"status": "permissive dependency policy verified", "binaries_checked": len(checked)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle")
    args = parser.parse_args()
    print(json.dumps(audit(args.bundle)))


if __name__ == "__main__":
    main()
