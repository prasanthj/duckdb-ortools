# Model format v1

`ortools_solve(model VARCHAR)` accepts JSON assembled from constants or DuckDB's JSON/STRUCT/LIST expressions. Each scalar input row contains a complete independent optimization problem. Use a materialized CTE when referencing the same solution several times; the function is volatile and performs real work.

```json
{
  "version": 1,
  "solver": "cp_sat",
  "maximize": true,
  "time_limit_ms": 10000,
  "threads": 1,
  "seed": 1,
  "variables": [
    {"id": "hours", "lower": 0, "upper": 40, "integer": true, "objective": 100}
  ],
  "constraints": [
    {"id": "available", "upper": 24, "terms": [{"variable": "hours", "coefficient": 1}]}
  ]
}
```

The objective is the sum of each variable times its objective coefficient. `maximize` defaults to false, `solver` to `cp_sat`, time limit to 10000 milliseconds, and threads/seed to 1. Variable lower bounds default to 0, upper bounds to unbounded, objective coefficients to 0, and integer flags to true for CP-SAT or false for GLOP. CP-SAT requires finite integer variable bounds. GLOP rejects integer variables and supports only one thread.

A constraint enforces `lower <= sum(coefficient * variable) <= upper`. Omitted or NULL constraint bounds mean negative/positive infinity. Empty terms describe a constant-zero constraint and may make a model infeasible. Duplicate variable IDs, constraint IDs, or repeated variables within a constraint are errors. Unknown fields and unknown variable references are errors. IDs must be nonempty, at most 1024 bytes, and must not start with `!`.

CP-SAT accepts an optional `enforce` array on each constraint. Each entry names a variable bounded by [0,1]; `!name` means its negation. The constraint applies when **all** listed literals are true. This expresses activation, precedence, conditional budgets, and other Boolean policies without choosing a numerical big-M constant.

An optional top-level `assumptions` array fixes Boolean literals for a CP-SAT solve. If infeasible, `result.conflicts` reports a sufficient subset of those assumptions. This core need not be minimal and is not an explanation of every conflicting constraint. Map the assumption IDs back to named policies in the calling application.

All numeric inputs must be finite and within ±(2^53−1), except unbounded fields expressed by omission/NULL. CP-SAT bounds, coefficients, objective coefficients and decision values use explicitly scaled integer units. The shared SQL result exposes DOUBLE values; do not use this format for integer values outside that exact range. OR-Tools validates integer expression overflow and reports `MODEL_INVALID` when a model cannot be represented safely.

Result fields:

| Field | Type | Meaning |
|---|---|---|
| status | VARCHAR | OPTIMAL, FEASIBLE, INFEASIBLE, MODEL_INVALID, UNKNOWN; GLOP may return UNBOUNDED |
| solver | VARCHAR | cp_sat or glop |
| objective | DOUBLE | Incumbent objective; NULL when no solution exists |
| best_bound | DOUBLE | CP-SAT search bound; GLOP objective when optimal; otherwise NULL |
| wall_time_ms | DOUBLE | Native model construction and solve time |
| values | MAP(VARCHAR, DOUBLE) | Variable IDs and decision values; empty without a solution |
| conflicts | VARCHAR[] | CP-SAT sufficient assumption core when available |
| message | VARCHAR | Solver diagnostic, potentially empty |

Input errors raise `InvalidInputException`; SQL NULL input returns SQL NULL. Cancellation interrupts native search and raises DuckDB's `InterruptException`. Time limits apply per solve, not per entire query. CP-SAT may return a feasible incumbent before proving optimality. GLOP uses numerical tolerances; verify operational constraints with an appropriate tolerance.

Models are limited to 64 MiB of input JSON, 100000 variables, 200000 constraints, 1000000 linear terms, 32 CP-SAT search threads, and a 60000 ms time limit. These are admission limits, not a hard process memory quota. Retained allocation input is bounded by 100000 edges and an estimated 64 MiB; solver/model memory is additional.

`ortools_allocate` requires DuckDB's optimizer to install a blocking input sort. This barrier gathers all UNION/input pipelines before the shared solve, and may spill through DuckDB's normal sort machinery. `PRAGMA disable_optimizer` therefore rejects allocation calls explicitly. The scalar model interface remains usable with the optimizer disabled.
