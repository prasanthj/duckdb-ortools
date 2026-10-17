"""Synthetic models for native scheduling and shared-capacity contracts."""

import json


def term(variable, coefficient=1):
    return {"variable": variable, "coefficient": coefficient}


def two_week_model(tag_adjusted=False):
    """Integer-hour policy: predecessors must finish in an earlier week.

    Mandatory QA is feasible in week 1 and is explicitly required there.
    Medium-priority work can start only after both high-priority drafts finish.
    Existing commitments are fixed deductions; a throttled tag removes 2h/week
    in this synthetic fixture. That amount is a declared policy, not inferred.
    """
    workers = {
        "A": {"skills": {"python", "review"}, "available": 10, "committed": 2},
        "B": {"skills": {"javascript", "qa"}, "available": 6, "committed": 0},
        "C": {"skills": {"python", "javascript"}, "available": 4, "committed": 0},
        "D": {"skills": {"math", "qa"}, "available": 4, "committed": 0},
    }
    buckets = {
        "draft_python": {"skills": {"python"}, "hours": 8, "value": 100, "predecessors": []},
        "draft_js": {"skills": {"javascript"}, "hours": 6, "value": 90, "predecessors": []},
        "review_python": {"skills": {"python", "review"}, "hours": 4, "value": 120, "predecessors": ["draft_python"]},
        "mandatory_qa": {"skills": {"qa"}, "hours": 2, "value": 80, "predecessors": []},
        "medium_math": {"skills": {"math"}, "hours": 4, "value": 200, "predecessors": ["draft_python", "draft_js"]},
    }
    variables, constraints, allocations = [], [], []
    capacities = {
        w: data["available"] - data["committed"] - (2 if tag_adjusted and w == "A" else 0)
        for w, data in workers.items()
    }

    def add(name, terms, **bounds):
        constraints.append({"id": name, "terms": terms, **bounds})

    for week in (1, 2):
        for bucket, demand in buckets.items():
            for worker, data in workers.items():
                if not demand["skills"].issubset(data["skills"]):
                    continue
                x, y = f"hours:{worker}:{bucket}:{week}", f"assigned:{worker}:{bucket}:{week}"
                cap = min(capacities[worker], demand["hours"])
                variables += [
                    {"id": x, "lower": 0, "upper": cap, "objective": demand["value"]},
                    {"id": y, "lower": 0, "upper": 1, "objective": 0},
                ]
                add("activate:" + x, [term(x), term(y, -cap)], upper=0)
                add("nonempty:" + x, [term(x), term(y, -1)], lower=0)
                allocations.append({"worker": worker, "bucket": bucket, "week": week, "hours": x, "assigned": y})
            add(
                f"one_tasker:{bucket}:{week}",
                [term(a["assigned"]) for a in allocations if a["bucket"] == bucket and a["week"] == week],
                upper=1,
            )
        for worker in workers:
            add(
                f"capacity:{worker}:{week}",
                [term(a["hours"]) for a in allocations if a["worker"] == worker and a["week"] == week],
                upper=capacities[worker],
            )
    for bucket, demand in buckets.items():
        add("demand:" + bucket, [term(a["hours"]) for a in allocations if a["bucket"] == bucket], upper=demand["hours"])
        for predecessor in demand["predecessors"]:
            for a in allocations:
                if a["bucket"] != bucket:
                    continue
                # If this assignment is active, all predecessor hours must
                # already have been allocated strictly before this week.
                add(
                    f"precedence:{predecessor}:{a['hours']}",
                    [term(p["hours"]) for p in allocations if p["bucket"] == predecessor and p["week"] < a["week"]],
                    lower=buckets[predecessor]["hours"],
                    enforce=[a["assigned"]],
                )
    add(
        "earliest_qa",
        [term(a["hours"]) for a in allocations if a["bucket"] == "mandatory_qa" and a["week"] == 1],
        lower=2,
    )
    return (
        {"version": 1, "solver": "cp_sat", "maximize": True, "variables": variables, "constraints": constraints},
        workers,
        buckets,
        allocations,
        capacities,
    )


def verify_plan(result, workers, buckets, allocations, capacities):
    """Check operational rules from the fixture, separately from model terms."""
    assert result["status"] == "OPTIMAL"
    values = result["values"]
    assigned = [{**a, "allocated": values[a["hours"]]} for a in allocations if values[a["hours"]] > 0]
    for a in assigned:
        assert buckets[a["bucket"]]["skills"].issubset(workers[a["worker"]]["skills"])
        for predecessor in buckets[a["bucket"]]["predecessors"]:
            prior = sum(p["allocated"] for p in assigned if p["bucket"] == predecessor and p["week"] < a["week"])
            assert prior == buckets[predecessor]["hours"]
    for week in (1, 2):
        for worker, capacity in capacities.items():
            assert sum(a["allocated"] for a in assigned if a["worker"] == worker and a["week"] == week) <= capacity
        for bucket in buckets:
            assert sum(a["bucket"] == bucket and a["week"] == week for a in assigned) <= 1
    for bucket, demand in buckets.items():
        assert sum(a["allocated"] for a in assigned if a["bucket"] == bucket) <= demand["hours"]
    assert sum(a["allocated"] for a in assigned if a["bucket"] == "mandatory_qa" and a["week"] == 1) == 2
    assert result["objective"] == sum(a["allocated"] * buckets[a["bucket"]]["value"] for a in assigned)
    return assigned


def run(connection):
    report = {"fixture": "synthetic matching contract", "scenarios": []}
    for adjusted in (False, True):
        model, workers, buckets, allocations, capacities = two_week_model(adjusted)
        result = connection.execute("SELECT ortools_solve(?)", [json.dumps(model)]).fetchone()[0]
        assignments = verify_plan(result, workers, buckets, allocations, capacities)
        # The baseline attains the full-demand upper bound. Throttling A
        # prevents finishing the 8h draft in week 1 under the one-tasker rule,
        # so neither review nor medium work can start within the horizon.
        optimum = 1500 if adjusted else 2780
        assert result["objective"] == optimum
        report["scenarios"].append(
            {
                "name": "tag_adjusted" if adjusted else "baseline",
                "result": result,
                "assignments": assignments,
                "hours": sum(a["allocated"] for a in assignments),
            }
        )
    # Movable capacity is conserved across projects. Locked commitments consume
    # two of A's six hours; B's two source hours may move to higher-value Target.
    connection.execute("""CREATE OR REPLACE TEMP TABLE portfolio_edges AS
        SELECT 'portfolio'::VARCHAR scenario_id, tasker::VARCHAR tasker_id, bucket::VARCHAR bucket_id,
               capacity::DOUBLE available_hours, demand::DOUBLE demand_hours, value::DOUBLE gap_cost_per_hour
        FROM (VALUES ('A','Target',4,6,100), ('B','Target',2,6,100), ('B','Source',2,2,30))
        t(tasker,bucket,capacity,demand,value)""")
    rows = connection.execute(
        "SELECT tasker_id, bucket_id, allocated_hours, objective FROM ortools_allocate((SELECT * FROM portfolio_edges)) ORDER BY 1,2"
    ).fetchall()
    assert rows == [("A", "Target", 4.0, 600.0), ("B", "Source", 0.0, 600.0), ("B", "Target", 2.0, 600.0)]
    report["portfolio"] = {
        "assignments": rows,
        "target_hours": 6,
        "source_hours_removed": 2,
        "source_gap_cost_added": 60,
        "locked_hours": 2,
    }
    return report
