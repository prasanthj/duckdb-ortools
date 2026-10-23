"""Build checksum-pinned OR-Tools sources with only permissive dependencies."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISABLED = ("COINOR", "GLPK", "SCIP", "HIGHS", "PDLP", "CPLEX", "GUROBI", "XPRESS")
PATCHES = {
    "zlib": "ZLIB-v1.3.1.patch",
    "bzip2": "bzip2.patch",
    "absl": "abseil-cpp-20250814.1.patch",
    "protobuf": "protobuf-v33.1.patch",
    "re2": "re2-2025-08-12.patch",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_source(name, entry, patch):
    archive = ROOT / "vendor/source-archives" / f"{name}-{entry['commit']}.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        print(f"Downloading pinned {name}", flush=True)
        urllib.request.urlretrieve(entry["url"], archive)
    if sha256(archive) != entry["sha256"]:
        raise SystemExit(f"Source checksum mismatch: {archive}")
    target = ROOT / "vendor/minimal-src" / name
    marker = target / ".source-receipt"
    signature = entry["sha256"] + sha256(patch)
    if marker.exists() and marker.read_text() == signature:
        return target
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            parts = Path(member.name).parts
            if len(parts) > 1:
                member.name = str(Path(*parts[1:]))
                package.extract(member, target, filter="data")
    subprocess.run(["patch", "-p1", "--forward", "-i", str(patch)], cwd=target, check=True)
    marker.write_text(signature)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", action="store_true", help="Build self-contained community extension dependencies")
    parser.add_argument("--cmake-arg", action="append", default=[])
    args = parser.parse_args()
    prefix = ROOT / "vendor" / ("ortools-minimal-static" if args.static else "ortools-minimal")
    lock_path = ROOT / "scripts/sources.json"
    lock = json.loads(lock_path.read_text())
    policy_patch = ROOT / "scripts/patches/ortools-minimal.patch"
    fingerprint = hashlib.sha256(
        lock_path.read_bytes()
        + policy_patch.read_bytes()
        + Path(__file__).read_bytes()
        + repr((platform.system(), platform.machine())).encode()
        + repr((args.static, args.cmake_arg)).encode()
    ).hexdigest()
    receipt = prefix / "build-receipt.json"
    if receipt.exists() and json.loads(receipt.read_text())["fingerprint"] == fingerprint:
        return
    source = fetch_source("ortools", lock["ortools"], policy_patch)
    dependencies = {name: fetch_source(name, lock[name], source / "patches" / patch) for name, patch in PATCHES.items()}
    build = ROOT / "vendor" / ("ortools-minimal-static-build" if args.static else "ortools-minimal-build")
    # A different dependency policy must never reuse installed libraries or cached
    # package locations from the old all-solvers distribution.
    if prefix.exists():
        shutil.rmtree(prefix)
    prefix.mkdir(parents=True)
    flags = [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        "-DBUILD_SHARED_LIBS=" + ("OFF" if args.static else "ON"),
        "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
        "-DBUILD_DEPS=OFF",
        "-DBUILD_TESTING=OFF",
        "-DBUILD_SAMPLES=OFF",
        "-DBUILD_EXAMPLES=OFF",
        "-DBUILD_FLATZINC=OFF",
        "-DBUILD_MATH_OPT=OFF",
        "-DBUILD_PYTHON=OFF",
        "-DBUILD_JAVA=OFF",
        "-DBUILD_DOTNET=OFF",
        "-DBUILD_DOC=OFF",
        "-DBUILD_Eigen3=OFF",
        "-DUSE_GLOP=ON",
        "-DFETCHCONTENT_FULLY_DISCONNECTED=ON",
        "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF",
        "-DCMAKE_INSTALL_RPATH=" + ("@loader_path" if platform.system() == "Darwin" else "$ORIGIN"),
    ]
    flags += [f"-DUSE_{name}=OFF" for name in DISABLED]
    flags += [f"-DBUILD_{name}=ON" for name in ("ZLIB", "BZip2", "absl", "Protobuf", "re2")]
    flags += [f"-DFETCHCONTENT_SOURCE_DIR_{name.upper()}={path}" for name, path in dependencies.items()]
    if args.static:
        flags.append("-DENABLE_STATIC_LIB=ON")
    flags += args.cmake_arg
    environment = {**os.environ, "OR_TOOLS_PATCH": "6755"}
    subprocess.run(
        ["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja", f"-DCMAKE_INSTALL_PREFIX={prefix}", *flags],
        check=True,
        env=environment,
    )
    subprocess.run(
        ["cmake", "--build", str(build), "--parallel", os.environ.get("ORTOOLS_BUILD_JOBS", "4")], check=True
    )
    subprocess.run(["cmake", "--install", str(build)], check=True)
    cache = dict(
        line.split("=", 1)
        for line in (build / "CMakeCache.txt").read_text().splitlines()
        if "=" in line and not line.startswith(("//", "#"))
    )
    for key in [*(f"USE_{name}" for name in DISABLED), "BUILD_Eigen3", "BUILD_MATH_OPT"]:
        if cache.get(key + ":BOOL") != "OFF":
            raise SystemExit(f"Forbidden dependency enabled or missing from cache: {key}")
    notices = prefix / "share/licenses"
    inventory = []
    for name, entry in lock.items():
        directory = source if name == "ortools" else dependencies[name]
        patch = policy_patch if name == "ortools" else source / "patches" / PATCHES[name]
        for relative in entry["license_files"]:
            target = notices / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(directory / relative, target)
        shutil.copy2(patch, notices / name / "applied-build.patch")
        inventory.append(
            {
                "name": name,
                **entry,
                "patch_sha256": sha256(patch),
                "patch": "scripts/patches/ortools-minimal.patch"
                if name == "ortools"
                else "upstream patches/" + PATCHES[name],
            }
        )
    shutil.copy2(lock_path, prefix / "sources.json")
    receipt.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "ortools_version": "9.15.6755",
                "linkage": "static" if args.static else "shared",
                "disabled_backends": list(DISABLED),
                "dependencies": inventory,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
