-- Build one independent staffing problem per input row from ordinary columns.
LOAD 'build/extension/ortools/ortools.duckdb_extension';

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
