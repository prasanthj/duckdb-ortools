#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv sync --frozen --python 3.11
uv run --frozen python scripts/dependencies.py
ortools_prefix="$PWD/vendor/ortools-minimal"
duckdb_version="${DUCKDB_VERSION:-1.5.6}"
case "$duckdb_version" in
  1.4.5) source_commit=f31be57c1845a8895169fd58142040be26d433cf ;;
  1.5.6) source_commit=069cc9f9b5be802405797faecc284961b07c70ef ;;
  2.0.0) source_commit=d54cbbe3df17e8e5ce68312f483b4155836112aa ;;
  *) echo "Supported DuckDB versions: 1.4.5, 1.5.6, 2.0.0"; exit 1 ;;
esac
source_dir="${DUCKDB_SOURCE_DIR:-$PWD/vendor/duckdb-$duckdb_version}"
if [ ! -d "$source_dir" ]; then
  git clone --depth 1 --branch "v$duckdb_version" https://github.com/duckdb/duckdb.git "$source_dir"
fi
[ "$(git -C "$source_dir" rev-parse HEAD)" = "$source_commit" ] || {
  echo "DuckDB source must match pinned v$duckdb_version"; exit 1;
}
case "$duckdb_version-$(uname -s)-$(uname -m)" in
  1.4.5-Linux-x86_64) platform=linux-amd64; archive_sha=1edaf33c5a74973e191479ba11a71eadc575884fbafbaf7b14c7f9e5989bcee0 ;;
  1.4.5-Linux-aarch64|1.4.5-Linux-arm64) platform=linux-arm64; archive_sha=33f1874c87d31ada3fd57acdd610f6707ad732782d3960a71f395f79c36eb703 ;;
  1.4.5-Darwin-x86_64) platform=osx-amd64; archive_sha=ca7742d71cf586a5ad3165056b6eab95298243ea7367a33d1f335dd0929d25e3 ;;
  1.4.5-Darwin-arm64) platform=osx-arm64; archive_sha=09dff4ba1958f74dbf1688b2ca901a517716a2a329e49759a13e5d6ec14e50fe ;;
  1.5.6-Linux-x86_64) platform=linux-amd64; archive_sha=ab3d1d33951b8cb8bdcae740d8e06adf66bfb7ee880a3f7052d7a35088e16d2d ;;
  1.5.6-Linux-aarch64|1.5.6-Linux-arm64) platform=linux-arm64; archive_sha=56249751117a4c04289764e06eb431352ce33fb3f8e2f30401122d4d0e184139 ;;
  1.5.6-Darwin-x86_64) platform=osx-amd64; archive_sha=ff1d5092d4a495e9da831c3a2de5f2ee37b5f87ae23ec7e9aa7adb434bd8a134 ;;
  1.5.6-Darwin-arm64) platform=osx-arm64; archive_sha=e77c382fba15e3f5c0b3265d12e3a4de534699fdb91bcc9d10d4a857a58103d5 ;;
  2.0.0-Linux-x86_64) platform=linux-amd64; archive_sha=112d62c980f5447cd303a70b3dacbc09acd41ccb4ef6d154e8629f543a0f8568 ;;
  2.0.0-Linux-aarch64|2.0.0-Linux-arm64) platform=linux-arm64; archive_sha=8171b4842e68a85b2e82b342d3bf7b542a88fd6666e0c18438d7b459012494ee ;;
  2.0.0-Darwin-x86_64) platform=osx-amd64; archive_sha=7e6101ddc8de73e8dcc66f790e658e07891165d026992b40ceba20e50b01f8ec ;;
  2.0.0-Darwin-arm64) platform=osx-arm64; archive_sha=c3a571e6d4a0da97534c631df463ab13c07f8cf78049625f5df936b7beaab9ce ;;
  *) echo "Unsupported native platform"; exit 1 ;;
esac
static_dir="${DUCKDB_STATIC_DIR:-$PWD/vendor/duckdb-static/$duckdb_version/$platform}"
archive="$static_dir.zip"
archive_name="static-libs-$platform.zip"
if [ "$duckdb_version" = 2.0.0 ]; then
  archive="$static_dir.tar.gz"
  archive_name="duckdb-static-libs-$platform.tar.gz"
fi
if [ ! -f "$static_dir/libduckdb_static.a" ]; then
  mkdir -p "$static_dir"
  curl --fail --location --retry 3 --output "$archive" "https://github.com/duckdb/duckdb/releases/download/v$duckdb_version/$archive_name"
  uv run --frozen python - "$archive" "$archive_sha" "$static_dir" <<'PY'
import hashlib, sys, tarfile, zipfile
from pathlib import Path
archive, expected, target = sys.argv[1:]
if hashlib.sha256(Path(archive).read_bytes()).hexdigest() != expected:
    raise SystemExit("DuckDB static library checksum mismatch")
if archive.endswith(".tar.gz"):
    with tarfile.open(archive) as package:
        package.extractall(target, filter="data")
else:
    with zipfile.ZipFile(archive) as package:
        package.extractall(target)
PY
fi
build_dir="${ORTOOLS_BUILD_DIR:-build}"
# Invalidate stale CMake package locations when switching dependency builds.
uv run --frozen python - "$build_dir" "$ortools_prefix/build-receipt.json" <<'PYCODE'
import hashlib, shutil, sys
from pathlib import Path
build, receipt = Path(sys.argv[1]), Path(sys.argv[2])
marker = build / '.ortools-dependency-signature'
signature = hashlib.sha256(receipt.read_bytes()).hexdigest()
if not marker.exists() or marker.read_text() != signature:
    (build / 'CMakeCache.txt').unlink(missing_ok=True)
    shutil.rmtree(build / 'CMakeFiles', ignore_errors=True)
    shutil.rmtree(build / 'backend-test', ignore_errors=True)
    build.mkdir(parents=True, exist_ok=True)
    marker.write_text(signature)
PYCODE
uv run --frozen cmake -S "$source_dir" -B "$build_dir" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=20 \
  -DBUILD_UNITTESTS=OFF -DBUILD_SHELL=OFF -DENABLE_JEMALLOC=OFF -DEXTENSION_STATIC_BUILD=OFF \
  -DDUCKDB_EXPLICIT_VERSION="v$duckdb_version" \
  -DORTOOLS_DUCKDB_STATIC_DIR="$static_dir" \
  -DCMAKE_PREFIX_PATH="$ortools_prefix" -Dortools_DIR="$ortools_prefix/lib/cmake/ortools" -DDUCKDB_EXTENSION_CONFIGS="$PWD/extension_config.cmake" "$@"
uv run --frozen cmake --build "$build_dir" --target ortools_loadable_extension --parallel "${ORTOOLS_BUILD_JOBS:-4}"
# The native extension and its dependency directory are one relocatable package.
uv run --frozen python - "$build_dir/extension/ortools/lib" "$source_dir" <<'PY'
import shutil, sys
from pathlib import Path
target = Path(sys.argv[1])
if target.exists():
    shutil.rmtree(target)
target.mkdir(parents=True)
prefix = Path('vendor/ortools-minimal')
shutil.copy2(prefix / 'build-receipt.json', target.parent / 'build-receipt.json')
duckdb_source = Path(sys.argv[2])
notices = target.parent / 'duckdb-notices'
if notices.exists():
    shutil.rmtree(notices)
notices.mkdir()
shutil.copy2(duckdb_source / 'LICENSE', notices / 'LICENSE')
for source in (duckdb_source / 'third_party').rglob('*'):
    if source.is_file() and source.name.lower().startswith(('license', 'copying', 'notice', 'copyright')):
        dest = notices / source.relative_to(duckdb_source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
for source in (prefix / 'lib').iterdir():
    if source.is_file() and ('.dylib' in source.name or '.so' in source.name):
        dest = target / source.name
        if source.is_symlink():
            if not dest.exists() and not dest.is_symlink():
                dest.symlink_to(source.readlink())
        else:
            shutil.copy2(source, dest)
PY

uv run --frozen cmake -S tests/native -B "$build_dir/backend-test" -G Ninja \
  -DCMAKE_PREFIX_PATH="$ortools_prefix" -DORTOOLS_BUNDLE_DIR="$(cd "$build_dir/extension/ortools" && pwd)"
uv run --frozen cmake --build "$build_dir/backend-test" --parallel "${ORTOOLS_BUILD_JOBS:-4}"
uv run --frozen python scripts/audit_dependencies.py "$build_dir/extension/ortools"
