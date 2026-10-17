-- Run in DuckDB with unsigned development extensions enabled.
LOAD 'build/extension/ortools/ortools.duckdb_extension';

CREATE TEMP TABLE eligible_edges AS
SELECT 'baseline'::VARCHAR AS scenario_id,
       tasker::VARCHAR AS tasker_id,
       bucket::VARCHAR AS bucket_id,
       capacity::DOUBLE AS available_hours,
       demand::DOUBLE AS demand_hours,
       value::DOUBLE AS gap_cost_per_hour
FROM (VALUES ('A','python',4.5,5.0,100.0),
             ('A','javascript',4.5,3.0,80.0),
             ('B','python',3.0,5.0,100.0)) edges(tasker,bucket,capacity,demand,value);

SELECT tasker_id, bucket_id, allocated_hours, status, objective
FROM ortools_allocate((SELECT * FROM eligible_edges))
ORDER BY tasker_id, bucket_id;

-- Derived values can be selected from each row's complete problem model.
WITH models AS (
  SELECT 'example' AS scenario_id, '{
    "version": 1,
    "solver": "cp_sat",
    "maximize": true,
    "variables": [
      {"id":"x","lower":0,"upper":10,"objective":3},
      {"id":"y","lower":0,"upper":10,"objective":4}
    ],
    "constraints": [
      {"id":"capacity","upper":14,"terms":[
        {"variable":"x","coefficient":1},
        {"variable":"y","coefficient":2}
      ]}
    ]
  }' AS model
), solved AS MATERIALIZED (
  SELECT scenario_id, ortools_solve(model) AS solution FROM models
)
SELECT scenario_id, solution.status, solution.values['x'] AS x,
       solution.values['y'] AS y, solution.objective
FROM solved;
