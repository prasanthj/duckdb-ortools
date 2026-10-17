# Verification

Tests execute DuckDB SQL against the native OR-Tools libraries. Python OR-Tools is not a runtime or test dependency.

## SQL contracts

- Integer optima checked against exhaustive enumeration.
- Fractional linear optimization and distinct unbounded/infeasible outcomes.
- CP-SAT enforcement literals, negated literals, and sufficient assumption cores.
- Malformed models, unknown fields/references, duplicate terms, numeric limits, and SQL NULLs.
- Independent models per input row and typed decision results.
- Shared capacity/demand across chunks and UNION pipelines.
- Cancellation during active native search.

The scheduling contract uses small synthetic inputs to check qualification, commitments, prior-week predecessor completion, required early work, capacity restrictions, and conservation across competing allocations. Operational rules are checked separately from the model's generated terms.

## Distribution contracts

The native factory probe attempts CBC and CLP creation, including long aliases, and requires every attempt to fail. GLOP and CP-SAT positive controls solve an optimization problem. SQL validation independently rejects unsupported backend names.

Recursive library audits permit only the declared dependency families and platform runtimes. They reject unknown libraries, unresolved dependencies, escaping symlinks, and absolute non-system loader paths. See the [dependency policy](dependencies.md).

Package verification extracts the archive into a new directory, audits its libraries, runs the factory probe against those libraries, and executes CP-SAT/GLOP SQL solves in a fresh process without loader-path overrides.

## Run locally

```sh
./build.sh
uv run pytest -q
uv run ruff check tests scripts
```

Use `ORTOOLS_EXTENSION` to select another build, with a matching DuckDB runtime. For example:

```sh
ORTOOLS_EXTENSION=build-1.4.5/extension/ortools/ortools.duckdb_extension \
  uv run --with duckdb==1.4.5 pytest -q
```

Native CI runs both supported DuckDB versions on Linux x86-64. The manual platform workflow builds, tests, packages, and verifies both versions on Linux/macOS x86-64 and ARM64. It retains workflow artifacts rather than publishing releases.
