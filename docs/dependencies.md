# Permissive native dependency policy

The extension's source code is Apache-2.0. Native bundles are built from the pinned sources in `scripts/sources.json`; the upstream OR-Tools all-solvers binary distribution is not used.

| Bundled component | License |
|---|---|
| OR-Tools 9.15.6755, including CP-SAT/GLOP | Apache-2.0 |
| Abseil | Apache-2.0 |
| Protobuf | BSD-3-Clause |
| Protobuf's bundled utf8_range | MIT |
| RE2 | BSD-3-Clause |
| zlib | Zlib |
| libbzip2 | bzip2-1.0.6 (permissive) |
| DuckDB and nlohmann/json | MIT |

The platform supplies its C/C++ runtime and system libraries; these are not redistributed inside our bundle. Retain the included copyright notices, licenses, and modification notices when redistributing the extension.

The archive includes the pinned DuckDB source tree's third-party license/notice files as well as the OR-Tools dependency notices. Earlier development archives lacking `build-receipt.json` used the upstream all-solvers distribution and do not satisfy this policy.

## Build boundary

`USE_COINOR`, `USE_GLPK`, `USE_SCIP`, `USE_HIGHS`, `USE_PDLP`, `USE_CPLEX`, `USE_GUROBI`, and `USE_XPRESS` are explicitly OFF. MathOpt, language bindings, examples, and upstream tests are disabled. Each required dependency is enabled individually; `BUILD_DEPS=ON` is deliberately avoided because it would also enable Eigen. The source build still contains OR-Tools' permissively licensed internal algorithms; the SQL API exposes CP-SAT and GLOP.

Upstream's unconditional Eigen dependency checks are patched to apply only when PDLP or MathOpt is enabled. Unused language model-builder wrappers are omitted when language bindings are disabled; those wrappers otherwise assume Gurobi is enabled and prevent a no-Gurobi link. Every source archive is checked against a committed SHA-256 before extraction. Build integration patches are either shipped with pinned OR-Tools or committed in `scripts/patches/`; their hashes and copies accompany the dependency license notices. FetchContent receives the prepared local sources with network fetching disabled. Source and license provenance is included in `build-receipt.json`.

The minimal installation uses a separate prefix from the former prebuilt distribution. A changed dependency receipt invalidates the extension's cached CMake package locations, and bundling recreates the shared-library directory so obsolete CBC/CLP libraries cannot survive a rebuild.

## Regression proof

`tests/native/solver_boundary.cpp` calls the real OR-Tools factory with CBC and CLP names and their long aliases. Every attempt must return no solver. Positive controls create GLOP and CP-SAT solvers and solve a small optimization problem. This test failed against the original all-solvers bundle: CBC could solve the problem and CLP could be instantiated. It must pass against the minimal source build.

`tests/test_distribution.py` executes that native test against the extension's adjacent shared libraries. The existing SQL invalid-input test also explicitly rejects `cbc` and `clp` model requests.

`scripts/audit_dependencies.py` checks the extension and every shipped shared library using `otool` or `readelf`. Only the listed native library families and platform runtimes are permitted. Unknown or non-permitted libraries, escaping symlinks, unresolved bundled dependencies, and absolute non-system loader paths fail the build. This also catches an unused EPL library accidentally copied into the package.

Release verification extracts the archive into a temporary directory, audits it again, and copies/runs the native factory test there without loader-path overrides. It then exercises both SQL solver backends in a fresh DuckDB process. The factory regression, binary dependency audit, and SQL tests protect different boundaries; checking configuration flags alone would not prove the package cannot create an EPL solver.
