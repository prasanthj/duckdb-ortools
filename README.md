# OR-Tools for DuckDB

[![Native build and tests](https://github.com/prasanthj/duckdb-ortools/actions/workflows/release.yml/badge.svg)](https://github.com/prasanthj/duckdb-ortools/actions/workflows/release.yml)
[![Native CI](https://github.com/prasanthj/duckdb-ortools/actions/workflows/ci.yml/badge.svg)](https://github.com/prasanthj/duckdb-ortools/actions/workflows/ci.yml)
[![Status: private development](https://img.shields.io/badge/status-private%20development-orange)](https://github.com/prasanthj/duckdb-ortools)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.4.5%20%7C%201.5.6-fff000?logo=duckdb&logoColor=black)](https://github.com/prasanthj/duckdb-ortools/actions/workflows/release.yml)
[![Native targets](https://img.shields.io/badge/native-macOS%20%7C%20Linux%20%C2%B7%20x86--64%20%7C%20ARM64-blue)](https://github.com/prasanthj/duckdb-ortools/actions/workflows/release.yml)
[![OR-Tools](https://img.shields.io/badge/OR--Tools-9.15-blue)](https://developers.google.com/optimization)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Native constraint solving and resource allocation inside DuckDB SQL. Turn ordinary columns into optimization models, solve them with Google's OR-Tools, and select assignments, allocated hours, objective values, and diagnostics as derived columns.

**From independent rows to coordinated plans.** Use CP-SAT for integer and Boolean decisions, or GLOP for continuous linear allocation. Define explicit capacity, eligibility, budget, and precedence constraints, then inspect the solution alongside its input columns.

Private development repository, ready to build and test locally. Development extensions are unsigned. There is no published release or community-extension installation yet.

## Why constraint solving in DuckDB?

Independent rankings cannot enforce shared resource limits. Workers compete for assignments, work buckets share demand, and budgets constrain the whole plan. Model those relationships explicitly and solve them in the same query workflow that prepares the inputs.

| Solver | Best at | SQL interface |
|---|---|---|
| **CP-SAT** | Integer and Boolean decisions, assignments, conditional policies, precedence | `ortools_solve` |
| **GLOP** | Continuous linear optimization, fractional allocation, minimum cost | `ortools_solve`, `ortools_allocate` |

The extension calls native C++ OR-Tools directly. It needs no AI provider, API key, or Python solver runtime. Python is used for build tooling, tests, and as an optional SQL client.

## Features

- **Columns in, decisions out:** construct a complete model from each input row and return a typed solution `STRUCT`.
- **Two native solvers:** CP-SAT for bounded integer/Boolean models; GLOP for continuous linear models.
- **Relational allocation:** enforce shared worker capacity and bucket demand across eligible edges, grouped by independent scenario.
- **Conditional policies:** activate CP-SAT constraints with Boolean enforcement literals.
- **Infeasibility diagnostics:** named assumptions can return a sufficient conflicting subset.
- **Explicit solution quality:** retain status, objective, search bound, elapsed time, and diagnostics.
- **Query cancellation:** interrupt active native search through DuckDB cancellation.
- **Permissive distribution:** pinned source dependencies, excluded solver backends, native factory regressions, and recursive package audits.
- **Native build matrix:** DuckDB 1.4.5 and 1.5.6 on macOS/Linux, x86-64 and ARM64.

## Quick start

### 1. Build and load

Requirements: Git, a C++20 toolchain, and [uv](https://docs.astral.sh/uv/). The build provisions Python tooling, CMake, and Ninja through the locked environment. The first build compiles the pinned OR-Tools dependencies; later builds reuse the verified installation.

```sh
git clone git@github.com:prasanthj/duckdb-ortools.git
cd duckdb-ortools
./build.sh
```

The default target is DuckDB 1.5.6. To build 1.4.5 separately:

```sh
DUCKDB_VERSION=1.4.5 ORTOOLS_BUILD_DIR=build-1.4.5 ./build.sh
```

Use a matching DuckDB runtime. For the default build, start Python with `uv run python` and open a connection:

```python
import duckdb

con = duckdb.connect(config={"allow_unsigned_extensions": "true"})
con.execute("LOAD 'build/extension/ortools/ortools.duckdb_extension'")
con.sql("SELECT ortools_version()").show()
```

Run SQL with `con.sql("""...""").show()`. A matching DuckDB CLI can load the same artifact with `duckdb -unsigned`:

```sql
LOAD 'build/extension/ortools/ortools.duckdb_extension';
SELECT ortools_version();
```

The extension and adjacent `lib/` directory must move together. Query execution calls C++ OR-Tools directly; Python is only a client/build/test tool.

### 2. Derive a solution from input columns

Each job below becomes an independent staffing problem. Minimize regular and overtime cost while meeting required hours:

```sql
WITH jobs(job_id, regular_capacity, overtime_capacity, required_hours, hourly_rate) AS (
  VALUES ('job_A', 3.0, 2.0, 5.0, 100.0),
         ('job_B', 6.0, 3.0, 4.0, 100.0),
         ('job_C', 4.0, 3.0, 10.0, 100.0)
), models AS (
  SELECT job_id, json_object(
    'version', 1,
    'solver', 'glop',
    'variables', json_array(
      json_object('id', 'regular', 'upper', regular_capacity, 'objective', hourly_rate),
      json_object('id', 'overtime', 'upper', overtime_capacity, 'objective', hourly_rate * 1.5)
    ),
    'constraints', json_array(json_object(
      'id', 'required_hours', 'lower', required_hours,
      'terms', json_array(
        json_object('variable', 'regular', 'coefficient', 1),
        json_object('variable', 'overtime', 'coefficient', 1)
      )
    ))
  )::VARCHAR AS model
  FROM jobs
), solved AS MATERIALIZED (
  SELECT job_id, ortools_solve(model) AS solution FROM models
)
SELECT job_id, solution.status,
       solution.values['regular'] AS regular_hours,
       solution.values['overtime'] AS overtime_hours,
       solution.objective AS minimum_cost
FROM solved
ORDER BY job_id;
```

| job_id | status | regular_hours | overtime_hours | minimum_cost |
|---|---|---:|---:|---:|
| job_A | OPTIMAL | 3 | 2 | 600 |
| job_B | OPTIMAL | 4 | 0 | 400 |
| job_C | INFEASIBLE | NULL | NULL | NULL |

The third job exceeds available capacity. It returns an explicit infeasible result. See the complete [derived-column SQL file](examples/derived_column.sql).

## SQL API

### Complete models: `ortools_solve`

`ortools_solve(model VARCHAR)` accepts a versioned JSON model and returns:

| Field | SQL type | Meaning |
|---|---|---|
| `status` | `VARCHAR` | `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `MODEL_INVALID`, `UNKNOWN`; GLOP may return `UNBOUNDED` |
| `solver` | `VARCHAR` | `cp_sat` or `glop` |
| `objective` | `DOUBLE` | Incumbent objective; NULL without a solution |
| `best_bound` | `DOUBLE` | CP-SAT search bound; GLOP objective when optimal; otherwise NULL |
| `wall_time_ms` | `DOUBLE` | Native model construction and solve time |
| `values` | `MAP(VARCHAR, DOUBLE)` | Named decision values; empty without a solution |
| `conflicts` | `VARCHAR[]` | Sufficient infeasible assumption core, when available |
| `message` | `VARCHAR` | Solver diagnostic, potentially empty |

For example, a bounded integer allocation:

```sql
WITH solved AS MATERIALIZED (
  SELECT ortools_solve('{
    "version": 1,
    "solver": "cp_sat",
    "maximize": true,
    "variables": [
      {"id":"x", "upper":10, "objective":3},
      {"id":"y", "upper":10, "objective":4}
    ],
    "constraints": [
      {"id":"capacity", "upper":14, "terms":[
        {"variable":"x", "coefficient":1},
        {"variable":"y", "coefficient":2}
      ]}
    ]
  }') AS solution
)
SELECT solution.status, solution.values['x'] AS x,
       solution.values['y'] AS y, solution.objective
FROM solved;
-- OPTIMAL, x = 10, y = 2, objective = 38
```

The objective sums each variable multiplied by its objective coefficient. Each constraint bounds a linear expression. Add Boolean `enforce` literals for conditional constraints and top-level `assumptions` for named policy diagnostics. A returned core is sufficient for infeasibility and need not be minimal. See the [complete model contract](docs/model.md).

### Shared capacity: `ortools_allocate`

`ortools_allocate(input_relation, solver := 'glop', time_limit_ms := 10000)` accepts these six columns in this order, with exact names and types:

| Column | Type | Meaning |
|---|---|---|
| `scenario_id` | `VARCHAR` | Independent alternative to solve |
| `tasker_id` | `VARCHAR` | Worker sharing capacity across eligible edges |
| `bucket_id` | `VARCHAR` | Work bucket sharing demand across eligible workers |
| `available_hours` | `DOUBLE` | Worker capacity after existing commitments |
| `demand_hours` | `DOUBLE` | Remaining bucket demand |
| `gap_cost_per_hour` | `DOUBLE` | Value of covering one hour on this edge |

```sql
CREATE TEMP TABLE eligible_edges AS
SELECT 'baseline'::VARCHAR AS scenario_id,
       tasker::VARCHAR AS tasker_id,
       bucket::VARCHAR AS bucket_id,
       capacity::DOUBLE AS available_hours,
       demand::DOUBLE AS demand_hours,
       value::DOUBLE AS gap_cost_per_hour
FROM (VALUES ('A', 'python',     4.5, 5.0, 100.0),
             ('A', 'javascript', 4.5, 3.0,  80.0),
             ('B', 'python',     3.0, 5.0, 100.0))
     edges(tasker, bucket, capacity, demand, value);

SELECT tasker_id, bucket_id, allocated_hours, status, objective
FROM ortools_allocate((SELECT * FROM eligible_edges))
ORDER BY tasker_id, bucket_id;
-- A/python = 2h, A/javascript = 2.5h, B/python = 3h.
-- OPTIMAL; scenario objective = 700, repeated on each returned edge.
```

The function maximizes avoided gap cost, returning all input columns plus `allocated_hours`, `status`, `objective`, `best_bound`, and `wall_time_ms`. Capacity and demand are shared within each scenario, including across input chunks and UNION pipelines. Scenarios are alternatives, not additional simultaneous capacity.

Only pass eligible edges and deduct existing commitments first. Repeated worker capacities and bucket demands must agree. Use GLOP for fractional hours; CP-SAT requires explicit integer units. Precedence and ownership policies belong in a complete `ortools_solve` model. See [allocation.sql](examples/allocation.sql).

### Runtime identity: `ortools_version`

`ortools_version()` reports extension/solver version information. Keep it with the model, input identity, and scenario assumptions when recording results.

## Optimization patterns

### Independent row models

Use `ortools_solve` when each input row describes a complete problem. Construct JSON from its columns and select solution fields as derived columns. Use a `MATERIALIZED` CTE when referencing the same solve more than once.

### Shared resource allocation

Use `ortools_allocate` for eligible edges with shared worker capacity and bucket demand. Include only eligible edges and deduct existing commitments before solving. More complex policies require an explicit `ortools_solve` model.

### Compare alternative policies

Build a complete model for each alternative and retain the scenario identifier with its result. Compare objectives only when units and policy assumptions are comparable. A feasible result is not a proof of optimality; inspect status and bound before selecting a plan.

## Model controls and numerical semantics

| Control | Default | Supported range / behavior |
|---|---|---|
| `version` | Required | `1` |
| `solver` | `cp_sat` | `cp_sat`, `glop` |
| `maximize` | `false` | Minimize by default |
| `time_limit_ms` | `10000` | 1–60000 per solve |
| `threads` | `1` | CP-SAT: 1–32; GLOP: 1 |
| `seed` | `1` | Nonnegative 32-bit integer |

CP-SAT requires finite integer variable bounds and explicitly scaled integer coefficients, objectives, and units. GLOP accepts continuous variables and fractional coefficients, with numerical tolerances. All numeric inputs must be finite and within ±(2^53−1); omitted/NULL bounds represent unbounded limits where the model permits them. The SQL result uses `DOUBLE` decision values.

Admission limits are 64 MiB model JSON, 100,000 variables, 200,000 constraints, and 1,000,000 linear terms. Relational allocation retains at most 100,000 edges and an estimated 64 MiB of input. These limits are not a hard solver-process memory quota. See [model.md](docs/model.md) for defaults, bounds, literals, and validation rules.

## Failure and safety semantics

- SQL NULL model input returns NULL. Allocation rejects NULL required fields.
- Unknown fields, unknown variable references, duplicate identifiers/terms, invalid numbers, and inconsistent or duplicate allocation edges raise SQL errors.
- `FEASIBLE` reports an incumbent without proof of optimality. `UNKNOWN` does not establish feasibility or infeasibility. Inspect status before consuming a decision.
- Results without a solution have a NULL objective and an empty value map. A missing map key selects NULL.
- Time limits apply per solve. DuckDB query cancellation interrupts native search and raises an interruption error.
- `ortools_solve` is volatile and solves each input row independently. Materialize once when selecting multiple result fields; there is no result cache.
- `ortools_allocate` needs the optimizer's blocking input barrier. `PRAGMA disable_optimizer` explicitly rejects allocation calls; scalar models remain usable.
- Both interfaces return decisions. Applying assignments or changing commitments is the calling application's responsibility.

## Build, test, and release

```sh
./build.sh
uv run pytest -q
uv run ruff check tests scripts
```

The native suite checks integer optima against exhaustive enumeration, fractional allocation, assumption cores, validation, row independence, chunk/UNION conservation, cancellation, scheduling, and excluded solver factories. Python OR-Tools is not a dependency. See [verification.md](docs/verification.md).

[Native CI](.github/workflows/ci.yml) runs on pushes/PRs for both DuckDB versions. The manually dispatched [native build and test workflow](.github/workflows/release.yml) builds all four platform targets, tests both versions, audits dependencies, and verifies extracted packages in a relocated directory. It uploads workflow artifacts; it does not publish GitHub Releases.

To package a local build, use the platform/version that actually produced it. For example, on macOS ARM64 with the default build:

```sh
uv run python scripts/package_release.py --platform osx_arm64 --duckdb-version 1.5.6
uv run python scripts/verify_package.py \
  dist/ortools-v0.1.0-duckdb-v1.5.6-osx_arm64.tar.gz \
  --backend-probe build/extension/ortools/ortools_backend_test
```

Packages include native dependencies, the build receipt, source/license notices, and a SHA-256 sidecar. Verification runs real CP-SAT/GLOP solves and the excluded-backend probe against extracted libraries without loader-path overrides.

## Repository layout

| Path | Purpose |
|---|---|
| [src/](src) | Native SQL registration, model validation, and solver execution |
| [docs/](docs) | Model contract, dependency policy, and verification |
| [examples/](examples) | Standalone SQL examples |
| [tests/](tests) | Native SQL, scheduling, allocation, and distribution contracts |
| [scripts/](scripts) | Dependency build, library audit, packaging, and relocation verification |
| [.github/workflows/](.github/workflows) | Native CI and platform build/test matrix |

## Current scope

Implemented: CP-SAT linear models with Boolean enforcement/assumptions, GLOP linear models, and relational capacity allocation. Assignment and precedence tests use explicit linear/Boolean formulations.

Routing APIs, specialized scheduling builders, incremental solves, automatic constraint generation, and result caching are not implemented.

## Upstream projects

- [Google OR-Tools](https://github.com/google/or-tools): optimization toolkit providing CP-SAT and GLOP.
- [DuckDB](https://github.com/duckdb/duckdb): analytical database hosting the native extension.

## License

The extension is [Apache-2.0](LICENSE). Bundled dependencies use Apache-2.0, BSD-3-Clause, MIT, Zlib, or the permissive bzip2 license. See [NOTICE](NOTICE) and the [dependency policy](docs/dependencies.md).

The distribution builds OR-Tools from [checksum-pinned sources](scripts/sources.json), with COIN-OR, GLPK, SCIP, HiGHS, PDLP/Eigen, and commercial adapters disabled. It does not bundle the upstream all-solvers binary distribution. Native regressions attempt CBC/CLP factory creation and require them to be unavailable; SQL rejects those backend names. Library audits reject prohibited or unknown dependencies, and extracted packages run the same native boundary check.
