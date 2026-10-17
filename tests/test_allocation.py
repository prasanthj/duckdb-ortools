import duckdb
import pytest


def test_capacity_is_global_across_chunks_and_scenarios_are_isolated(connection):
    # 5000 edges cross multiple DuckDB input/output chunks. Chunk-local solves
    # would spend the same tasker's one available hour repeatedly.
    connection.execute("""CREATE TABLE edges AS
        SELECT scenario::VARCHAR scenario_id, 'worker'::VARCHAR tasker_id,
               bucket::VARCHAR bucket_id, 1.0::DOUBLE available_hours,
               1.0::DOUBLE demand_hours, (bucket + 1)::DOUBLE gap_cost_per_hour
        FROM range(2) s(scenario) CROSS JOIN range(5000) b(bucket)""")
    rows = connection.execute("""SELECT scenario_id, count(*), sum(allocated_hours), sum(allocated_hours * gap_cost_per_hour), min(status)
        FROM ortools_allocate((SELECT * FROM edges WHERE bucket_id::INTEGER < 2500
            UNION ALL SELECT * FROM edges WHERE bucket_id::INTEGER >= 2500))
        GROUP BY scenario_id ORDER BY scenario_id""").fetchall()
    assert rows == [("0", 5000, 1.0, 5000.0, "OPTIMAL"), ("1", 5000, 1.0, 5000.0, "OPTIMAL")]


@pytest.mark.parametrize("backend", ["glop", "cp_sat"])
def test_shared_capacity_and_eligibility_choose_global_optimum(connection, backend):
    # Greedy A->X (10) blocks B, whereas A->Y (9) + B->X (8) earns 17.
    connection.execute("""CREATE TABLE edges AS SELECT 'base'::VARCHAR scenario_id,
        worker::VARCHAR tasker_id, bucket::VARCHAR bucket_id, 1.0::DOUBLE available_hours,
        1.0::DOUBLE demand_hours, cost::DOUBLE gap_cost_per_hour
        FROM (VALUES ('A','X',10), ('A','Y',9), ('B','X',8)) t(worker,bucket,cost)""")
    rows = connection.execute(
        f"SELECT tasker_id, bucket_id, allocated_hours FROM ortools_allocate((SELECT * FROM edges), solver := '{backend}') ORDER BY 1,2"
    ).fetchall()
    assert rows == [("A", "X", 0.0), ("A", "Y", 1.0), ("B", "X", 1.0)]


def test_fractional_capacity_and_rejected_ambiguous_edges(connection):
    connection.execute("""CREATE TABLE edges AS SELECT 'base'::VARCHAR scenario_id, 'A'::VARCHAR tasker_id,
        'X'::VARCHAR bucket_id, 3.25::DOUBLE available_hours, 5.5::DOUBLE demand_hours, 2.4::DOUBLE gap_cost_per_hour""")
    assert connection.execute(
        "SELECT allocated_hours, objective FROM ortools_allocate((SELECT * FROM edges))"
    ).fetchone() == pytest.approx((3.25, 7.8))
    with pytest.raises(duckdb.InvalidInputException, match="exact integer"):
        connection.execute("SELECT * FROM ortools_allocate((SELECT * FROM edges), solver := 'cp_sat')").fetchall()
    with pytest.raises(duckdb.InvalidInputException, match="duplicate"):
        connection.execute(
            "SELECT * FROM ortools_allocate((SELECT * FROM edges UNION ALL SELECT * FROM edges))"
        ).fetchall()
    with pytest.raises(duckdb.InvalidInputException, match="inconsistent"):
        connection.execute("""SELECT * FROM ortools_allocate((SELECT * FROM edges UNION ALL
            SELECT scenario_id, tasker_id, 'Y', 4.0::DOUBLE, demand_hours, gap_cost_per_hour FROM edges))""").fetchall()
    with pytest.raises(duckdb.InvalidInputException, match="NULL"):
        connection.execute(
            "SELECT * FROM ortools_allocate((SELECT scenario_id, NULL::VARCHAR tasker_id, bucket_id, available_hours, demand_hours, gap_cost_per_hour FROM edges))"
        ).fetchall()
    assert connection.execute(
        "SELECT count(*) FROM ortools_allocate((SELECT * FROM edges WHERE false))"
    ).fetchone() == (0,)
    connection.execute("PRAGMA disable_optimizer")
    with pytest.raises(duckdb.BinderException, match="materialization barrier"):
        connection.execute("SELECT * FROM ortools_allocate((SELECT * FROM edges))").fetchall()
