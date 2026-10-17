"""Public SQL contracts, independently checked rather than mirrored solver code."""

import copy
import itertools
import json
import random
import threading
import time

import duckdb
import pytest


def solve(connection, model):
    return connection.execute("SELECT ortools_solve(?)", [json.dumps(model)]).fetchone()[0]


def variable(name, upper=4, objective=1, **extra):
    return {"id": name, "lower": 0, "upper": upper, "objective": objective, **extra}


def constraint(name, terms, **bounds):
    return {"id": name, "terms": [{"variable": key, "coefficient": value} for key, value in terms.items()], **bounds}


def test_integer_models_match_exhaustive_enumeration(connection):
    rng = random.Random(41)
    for iteration in range(15):
        names = ["a", "b", "c", "d"]
        coefficients = [rng.randint(-3, 7) for _ in names]
        rows = [[rng.randint(-2, 4) for _ in names] for _ in range(5)]
        bounds = [rng.randint(0, 14) for _ in rows]
        feasible = [
            values
            for values in itertools.product(range(5), repeat=4)
            if all(sum(a * x for a, x in zip(row, values)) <= bound for row, bound in zip(rows, bounds))
        ]
        optimum = max(sum(c * x for c, x in zip(coefficients, values)) for values in feasible)
        model = {
            "version": 1,
            "maximize": True,
            "variables": [variable(n, objective=c) for n, c in zip(names, coefficients)],
            "constraints": [
                constraint(f"r{i}", dict(zip(names, row)), upper=b) for i, (row, b) in enumerate(zip(rows, bounds))
            ],
        }
        result = solve(connection, model)
        assert result["status"] == "OPTIMAL", iteration
        assert result["objective"] == result["best_bound"] == optimum
        actual = tuple(result["values"][name] for name in names)
        assert actual in feasible
        assert sum(c * x for c, x in zip(coefficients, actual)) == optimum


def test_fractional_linear_program_and_unbounded_status(connection):
    model = {
        "version": 1,
        "solver": "glop",
        "maximize": True,
        "variables": [
            variable("x", upper=100, objective=3, integer=False),
            variable("y", upper=100, objective=4, integer=False),
        ],
        "constraints": [
            constraint("resource", {"x": 1, "y": 2}, upper=14),
            constraint("ratio", {"x": 3, "y": -1}, lower=0),
            constraint("difference", {"x": 1, "y": -1}, upper=2),
        ],
    }
    result = solve(connection, model)
    assert result["status"] == "OPTIMAL"
    assert result["values"] == pytest.approx({"x": 6, "y": 4})
    assert result["objective"] == pytest.approx(34)
    model["variables"] = [variable("x", upper=None, objective=1, integer=False)]
    model["constraints"] = []
    result = solve(connection, model)
    assert result["status"] == "UNBOUNDED"
    assert result["values"] == {} and result["objective"] is None
    model["constraints"] = [constraint("minimum", {"x": 1}, lower=2), constraint("maximum", {"x": 1}, upper=1)]
    result = solve(connection, model)
    assert result["status"] == "INFEASIBLE"
    assert result["values"] == {} and result["objective"] is None


def test_infeasibility_core_and_enforcement(connection):
    model = {
        "version": 1,
        "variables": [variable("x", 1), variable("zero_rule", 1, objective=0), variable("one_rule", 1, objective=0)],
        "constraints": [
            constraint("zero", {"x": 1}, upper=0, enforce=["zero_rule"]),
            constraint("one", {"x": 1}, lower=1, enforce=["one_rule"]),
        ],
        "assumptions": ["zero_rule", "one_rule"],
    }
    result = solve(connection, model)
    assert result["status"] == "INFEASIBLE"
    assert result["values"] == {} and result["objective"] is None and result["best_bound"] is None
    assert set(result["conflicts"]) == {"zero_rule", "one_rule"}
    model["assumptions"] = ["zero_rule", "!one_rule"]
    result = solve(connection, model)
    assert result["status"] == "OPTIMAL" and result["values"]["x"] == 0


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"version": 2}, "version"),
        ({"solver": "missing"}, "solver"),
        ({"solver": "cbc"}, "solver"),
        ({"solver": "clp"}, "solver"),
        ({"time_limit_ms": 0}, "time_limit_ms"),
        ({"threads": 33}, "threads"),
        ({"maximise": True}, "unknown field"),
        ({"variables": [variable("x", 1.5)]}, "exact integer"),
        ({"variables": [variable("x"), variable("x")]}, "duplicate variable"),
        ({"variables": [variable("x", integer=False)]}, "continuous"),
        ({"constraints": [constraint("bad", {"missing": 1}, upper=1)]}, "unknown variable"),
        ({"constraints": [constraint("bad", {"x": 0.25}, upper=1)]}, "exact integer"),
        ({"constraints": [constraint("bad", {"x": 1}, lower=2, upper=1)]}, "lower exceeds"),
    ],
)
def test_invalid_models_fail_at_sql_boundary(connection, mutation, message):
    model = {"version": 1, "variables": [variable("x")], "constraints": []}
    model.update(copy.deepcopy(mutation))
    with pytest.raises(duckdb.InvalidInputException, match=message):
        solve(connection, model)


def test_nested_results_nulls_and_independent_row_models(connection):
    models = [
        json.dumps({"version": 1, "maximize": True, "variables": [variable("x", n)], "constraints": []}) for n in (2, 5)
    ]
    result = connection.execute(
        "SELECT (ortools_solve(model)).values['x'] FROM (VALUES (?), (?), (NULL)) t(model)", models
    ).fetchall()
    assert result == [(2.0,), (5.0,), (None,)]


def test_cancel_interrupts_active_native_search(connection):
    rng = random.Random(101)
    names = [f"x{i}" for i in range(600)]
    model = {
        "version": 1,
        "maximize": True,
        "time_limit_ms": 60000,
        "variables": [variable(n, 1) for n in names],
        "constraints": [
            constraint(f"e{i}:{j}", {names[i]: 1, names[j]: 1}, upper=1)
            for i in range(len(names))
            for j in range(i)
            if rng.random() < 0.12
        ],
    }
    errors = []

    def execute():
        try:
            solve(connection, model)
        except duckdb.InterruptException as error:
            errors.append(error)

    thread = threading.Thread(target=execute)
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive(), "the workload must still be running to prove active cancellation"
    connection.interrupt()
    thread.join(timeout=5)
    assert not thread.is_alive() and len(errors) == 1
    assert connection.execute("SELECT 42").fetchone() == (42,)
