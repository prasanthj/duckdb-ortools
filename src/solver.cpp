#include "solver.hpp"
#include "duckdb/main/client_context.hpp"
#include "ortools/linear_solver/linear_solver.h"
#include "ortools/sat/cp_model.h"
#include "ortools/sat/cp_model_solver.h"
#include "ortools/sat/swig_helper.h"
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <set>
#include <thread>

namespace duckdb {
namespace sat = operations_research::sat;
using operations_research::MPSolver;
static constexpr double EXACT_INTEGER = 9007199254740991.0;

bool SolverInterrupted(ClientContext &context) {
#ifdef ORTOOLS_DUCKDB_1_4
  return context.interrupted.load();
#else
  return context.IsInterrupted();
#endif
}

static void Require(bool condition, const string &message) {
  if (!condition)
    throw InvalidInputException("ortools: " + message);
}
static void Fields(const Json &object, std::initializer_list<const char *> allowed, const string &where) {
  Require(object.is_object(), where + " must be an object");
  std::set<string> keys;
  for (auto key : allowed)
    keys.insert(key);
  for (auto it = object.begin(); it != object.end(); ++it)
    Require(keys.count(it.key()) > 0, where + ": unknown field '" + it.key() + "'");
}
static string Identifier(const Json &node, const string &where) {
  Require(node.is_string(), where + " must be a string");
  auto id = node.get<string>();
  Require(!id.empty() && id.size() <= 1024 && id[0] != '!', where + " must contain 1–1024 bytes and not start with !");
  return id;
}
static double Number(const Json &node, const string &where) {
  Require(node.is_number(), where + " must be numeric");
  double value = node.get<double>();
  Require(std::isfinite(value) && std::abs(value) <= EXACT_INTEGER, where + " must be finite and within ±(2^53-1)");
  return value;
}
static int64_t Integer(double value, const string &where) {
  Require(std::isfinite(value) && std::abs(value) <= EXACT_INTEGER && std::trunc(value) == value,
          where + " must be an exact integer for CP-SAT; explicitly scale fractional units first");
  return static_cast<int64_t>(value);
}
static double Bound(const Json &object, const char *key, double fallback) {
  return !object.contains(key) || object.at(key).is_null() ? fallback : Number(object.at(key), key);
}

// The watcher only requests cancellation; it never accesses DuckDB vectors.
class Cancellation {
public:
  Cancellation(ClientContext &context, std::function<void()> interrupt)
      : watcher([&, interrupt] {
          std::unique_lock<std::mutex> lock(mutex);
          while (!done) {
            if (cv.wait_for(lock, std::chrono::milliseconds(10), [&] { return done; }))
              break;
            if (SolverInterrupted(context)) {
              interrupt();
              break;
            }
          }
        }) {}
  ~Cancellation() {
    {
      std::lock_guard<std::mutex> lock(mutex);
      done = true;
    }
    cv.notify_one();
    watcher.join();
  }

private:
  std::mutex mutex;
  std::condition_variable cv;
  bool done = false;
  std::thread watcher;
};

struct Variable {
  string id;
  double lower, upper, objective;
  bool integer;
};
struct ModelConstraint {
  string id;
  double lower, upper;
  vector<std::pair<idx_t, double>> terms;
  vector<string> enforce;
};

SolverResult SolveModel(ClientContext &context, const Json &doc) {
  if (SolverInterrupted(context))
    throw InterruptException();
  Fields(
      doc,
      {"version", "solver", "variables", "constraints", "maximize", "time_limit_ms", "threads", "seed", "assumptions"},
      "model");
  Require(doc.contains("version") && doc.at("version").is_number_integer() && doc.at("version") == 1,
          "model.version must be 1");
  const string backend = doc.value("solver", string("cp_sat"));
  Require(backend == "cp_sat" || backend == "glop", "solver must be cp_sat or glop");
  const bool cp = backend == "cp_sat";
  const bool maximize = doc.value("maximize", false);
  const auto time_ms = Integer(Number(doc.value("time_limit_ms", Json(10000)), "time_limit_ms"), "time_limit_ms");
  const auto threads = Integer(Number(doc.value("threads", Json(1)), "threads"), "threads");
  const auto seed = Integer(Number(doc.value("seed", Json(1)), "seed"), "seed");
  Require(time_ms >= 1 && time_ms <= 60000, "time_limit_ms must be in [1,60000]");
  Require(threads >= 1 && threads <= 32, "threads must be in [1,32]");
  Require(seed >= 0 && seed <= INT32_MAX, "seed must be in [0,2147483647]");
  Require(cp || threads == 1, "GLOP supports threads=1");
  const auto &vars = doc.at("variables");
  const auto &cons = doc.at("constraints");
  Require(vars.is_array() && !vars.empty() && vars.size() <= 100000, "variables must contain 1–100000 entries");
  Require(cons.is_array() && cons.size() <= 200000, "constraints must contain at most 200000 entries");
  vector<Variable> variables;
  std::map<string, idx_t> indices;
  for (const auto &v : vars) {
    Fields(v, {"id", "lower", "upper", "objective", "integer"}, "variable");
    Variable parsed{Identifier(v.at("id"), "variable.id"), Bound(v, "lower", 0),
                    Bound(v, "upper", std::numeric_limits<double>::infinity()),
                    Number(v.value("objective", Json(0)), "objective"), v.value("integer", cp)};
    Require(parsed.lower <= parsed.upper, "variable " + parsed.id + ": lower exceeds upper");
    Require(indices.emplace(parsed.id, variables.size()).second, "duplicate variable '" + parsed.id + "'");
    Require(cp || !parsed.integer, "GLOP cannot enforce integer variables; use cp_sat");
    Require(!cp || parsed.integer, "CP-SAT cannot enforce continuous variables; use glop");
    if (cp) {
      Integer(parsed.lower, "variable.lower");
      Integer(parsed.upper, "variable.upper");
      Integer(parsed.objective, "variable.objective");
    }
    variables.push_back(parsed);
  }
  auto index = [&](const string &id) {
    auto it = indices.find(id);
    Require(it != indices.end(), "unknown variable '" + id + "'");
    return it->second;
  };
  auto literal = [&](const string &name) {
    const string id = name[0] == '!' ? name.substr(1) : name;
    idx_t i = index(id);
    Require(variables[i].lower == 0 && variables[i].upper == 1, "literal '" + name + "' requires a [0,1] variable");
    return std::make_pair(i, name[0] == '!');
  };
  vector<ModelConstraint> constraints;
  std::set<string> names;
  idx_t term_count = 0;
  for (const auto &c : cons) {
    Fields(c, {"id", "lower", "upper", "terms", "enforce"}, "constraint");
    ModelConstraint parsed;
    parsed.id = Identifier(c.at("id"), "constraint.id");
    Require(names.insert(parsed.id).second, "duplicate constraint '" + parsed.id + "'");
    parsed.lower = Bound(c, "lower", -std::numeric_limits<double>::infinity());
    parsed.upper = Bound(c, "upper", std::numeric_limits<double>::infinity());
    Require(parsed.lower <= parsed.upper, "constraint " + parsed.id + ": lower exceeds upper");
    Require(c.at("terms").is_array(), "constraint.terms must be an array");
    std::set<idx_t> seen;
    for (const auto &term : c.at("terms")) {
      Fields(term, {"variable", "coefficient"}, "term");
      idx_t i = index(Identifier(term.at("variable"), "term.variable"));
      Require(seen.insert(i).second, "duplicate term in constraint '" + parsed.id + "'");
      const double coefficient = Number(term.at("coefficient"), "term.coefficient");
      if (cp)
        Integer(coefficient, "term.coefficient");
      parsed.terms.emplace_back(i, coefficient);
      Require(++term_count <= 1000000, "model exceeds 1000000 terms");
    }
    if (c.contains("enforce")) {
      Require(cp, "enforced constraints require cp_sat");
      Require(c.at("enforce").is_array(), "enforce must be a list of Boolean literal names");
      for (const auto &value : c.at("enforce")) {
        Require(value.is_string() && !value.get<string>().empty(), "enforce requires nonempty literal names");
        parsed.enforce.push_back(value.get<string>());
        literal(parsed.enforce.back());
      }
    }
    constraints.push_back(std::move(parsed));
  }
  vector<string> assumptions;
  if (doc.contains("assumptions")) {
    Require(cp && doc.at("assumptions").is_array(), "assumptions requires cp_sat and an array of literals");
    for (const auto &v : doc.at("assumptions")) {
      Require(v.is_string() && !v.get<string>().empty(), "assumptions requires nonempty literal names");
      assumptions.push_back(v.get<string>());
      literal(assumptions.back());
    }
  }
  SolverResult result;
  result.solver = backend;
  auto started = std::chrono::steady_clock::now();
  if (cp) {
    sat::CpModelBuilder model;
    vector<sat::IntVar> handles;
    for (const auto &v : variables)
      handles.push_back(
          model.NewIntVar(operations_research::Domain(Integer(v.lower, "lower"), Integer(v.upper, "upper")))
              .WithName(v.id));
    auto boolean = [&](const string &name) {
      auto [i, negated] = literal(name);
      auto b = model.GetBoolVarFromProtoIndex(handles[i].index());
      return negated ? b.Not() : b;
    };
    for (const auto &c : constraints) {
      sat::LinearExpr expression;
      for (const auto &[i, coefficient] : c.terms)
        expression += handles[i] * Integer(coefficient, "coefficient");
      const int64_t lower = std::isfinite(c.lower) ? Integer(c.lower, "constraint.lower") : INT64_MIN;
      const int64_t upper = std::isfinite(c.upper) ? Integer(c.upper, "constraint.upper") : INT64_MAX;
      auto constraint = model.AddLinearConstraint(expression, operations_research::Domain(lower, upper)).WithName(c.id);
      for (const auto &name : c.enforce)
        constraint.OnlyEnforceIf(boolean(name));
    }
    for (const auto &name : assumptions)
      model.AddAssumption(boolean(name));
    sat::LinearExpr objective;
    for (idx_t i = 0; i < variables.size(); i++)
      objective += handles[i] * Integer(variables[i].objective, "objective");
    if (maximize)
      model.Maximize(objective);
    else
      model.Minimize(objective);
    sat::SatParameters parameters;
    parameters.set_max_time_in_seconds(time_ms / 1000.0);
    parameters.set_num_search_workers(threads);
    parameters.set_random_seed(seed);
    // Keep Model's address-based type keys inside libortools. Hidden symbols
    // in a loadable extension otherwise create a different TimeLimit singleton
    // on ELF, so an external stop flag would never reach the active search.
    sat::SolveWrapper solver;
    solver.SetParameters(parameters);
    sat::CpSolverResponse response;
    {
      Cancellation cancellation(context, [&] { solver.StopSearch(); });
      response = solver.Solve(model.Build());
    }
    result.status = sat::CpSolverStatus_Name(response.status());
    result.message = response.solution_info();
    if (response.status() == sat::CpSolverStatus::OPTIMAL || response.status() == sat::CpSolverStatus::FEASIBLE) {
      result.objective = response.objective_value();
      result.best_bound = response.best_objective_bound();
      for (idx_t i = 0; i < variables.size(); i++)
        result.values[variables[i].id] = sat::SolutionIntegerValue(response, handles[i]);
    }
    for (int encoded : response.sufficient_assumptions_for_infeasibility()) {
      const idx_t i = encoded >= 0 ? encoded : -encoded - 1;
      result.conflicts.push_back((encoded >= 0 ? "" : "!") + variables.at(i).id);
    }
  } else {
    MPSolver solver("duckdb_ortools", MPSolver::GLOP_LINEAR_PROGRAMMING);
    // GLOP presolve reports some unbounded models as INFEASIBLE. Preserve the
    // public status distinction rather than returning a false infeasibility.
    solver.SetSolverSpecificParametersAsString("use_preprocessing: false");
    vector<operations_research::MPVariable *> handles;
    for (const auto &v : variables) {
      handles.push_back(solver.MakeNumVar(v.lower, v.upper, v.id));
      solver.MutableObjective()->SetCoefficient(handles.back(), v.objective);
    }
    for (const auto &c : constraints) {
      auto *constraint = solver.MakeRowConstraint(c.lower, c.upper, c.id);
      for (const auto &[i, coefficient] : c.terms)
        constraint->SetCoefficient(handles[i], coefficient);
    }
    solver.MutableObjective()->SetOptimizationDirection(maximize);
    solver.SetTimeLimit(absl::Milliseconds(time_ms));
    MPSolver::ResultStatus status;
    {
      Cancellation cancellation(context, [&] { solver.InterruptSolve(); });
      status = solver.Solve();
    }
    switch (status) {
    case MPSolver::OPTIMAL:
      result.status = "OPTIMAL";
      break;
    case MPSolver::FEASIBLE:
      result.status = "FEASIBLE";
      break;
    case MPSolver::INFEASIBLE:
      result.status = "INFEASIBLE";
      break;
    case MPSolver::UNBOUNDED:
      result.status = "UNBOUNDED";
      break;
    case MPSolver::MODEL_INVALID:
      result.status = "MODEL_INVALID";
      break;
    default:
      result.status = "UNKNOWN";
    }
    if (status == MPSolver::OPTIMAL || status == MPSolver::FEASIBLE) {
      result.objective = solver.Objective().Value();
      if (status == MPSolver::OPTIMAL)
        result.best_bound = result.objective;
      for (idx_t i = 0; i < variables.size(); i++)
        result.values[variables[i].id] = handles[i]->solution_value();
    }
  }
  if (SolverInterrupted(context))
    throw InterruptException();
  result.wall_time_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
  return result;
}

LogicalType SolverResultType() {
  return LogicalType::STRUCT({{"status", LogicalType::VARCHAR},
                              {"solver", LogicalType::VARCHAR},
                              {"objective", LogicalType::DOUBLE},
                              {"best_bound", LogicalType::DOUBLE},
                              {"wall_time_ms", LogicalType::DOUBLE},
                              {"values", LogicalType::MAP(LogicalType::VARCHAR, LogicalType::DOUBLE)},
                              {"conflicts", LogicalType::LIST(LogicalType::VARCHAR)},
                              {"message", LogicalType::VARCHAR}});
}
Value SolverResultValue(const SolverResult &r) {
  vector<Value> keys, values, conflicts;
  for (const auto &[key, value] : r.values) {
    keys.emplace_back(key);
    values.push_back(Value::DOUBLE(value));
  }
  for (const auto &name : r.conflicts)
    conflicts.emplace_back(name);
  return Value::STRUCT({{"status", Value(r.status)},
                        {"solver", Value(r.solver)},
                        {"objective", r.objective ? Value::DOUBLE(*r.objective) : Value(LogicalType::DOUBLE)},
                        {"best_bound", r.best_bound ? Value::DOUBLE(*r.best_bound) : Value(LogicalType::DOUBLE)},
                        {"wall_time_ms", Value::DOUBLE(r.wall_time_ms)},
                        {"values", Value::MAP(LogicalType::VARCHAR, LogicalType::DOUBLE, keys, values)},
                        {"conflicts", Value::LIST(LogicalType::VARCHAR, conflicts)},
                        {"message", Value(r.message)}});
}
} // namespace duckdb
