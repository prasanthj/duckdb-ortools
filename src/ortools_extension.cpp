#define DUCKDB_EXTENSION_MAIN
#include "ortools_extension.hpp"
#include "duckdb/function/table_function.hpp"
#include "duckdb/main/client_config.hpp"
#include "duckdb/main/config.hpp"
#include "duckdb/main/extension/extension_loader.hpp"
#include "duckdb/optimizer/optimizer_extension.hpp"
#include "duckdb/planner/expression/bound_columnref_expression.hpp"
#include "duckdb/planner/operator/logical_get.hpp"
#include "duckdb/planner/operator/logical_order.hpp"
#include "solver.hpp"
#ifdef ORTOOLS_DUCKDB_2
#include "duckdb/common/identifier.hpp"
#include "duckdb/main/settings.hpp"
#endif
#include <cmath>
#include <set>

namespace duckdb {
#ifdef ORTOOLS_DUCKDB_2
using SqlIdentifier = Identifier;
#else
using SqlIdentifier = string;
#endif
static constexpr idx_t MAX_BYTES = 64 * 1024 * 1024;

static void SolveScalar(DataChunk &input, ExpressionState &state, Vector &output) {
  auto &context = state.GetContext();
  for (idx_t row = 0; row < input.size(); row++) {
    auto value = input.GetValue(0, row);
    if (value.IsNull()) {
      output.SetValue(row, Value(SolverResultType()));
      continue;
    }
    const auto model = value.GetValue<string>();
    if (model.size() > MAX_BYTES)
      throw InvalidInputException("ortools: model exceeds 64 MiB");
    try {
      output.SetValue(row, SolverResultValue(SolveModel(context, Json::parse(model))));
    } catch (const Json::exception &e) {
      throw InvalidInputException("ortools: invalid model JSON: " + string(e.what()));
    }
  }
}
static void VersionScalar(DataChunk &input, ExpressionState &, Vector &output) {
  output.SetVectorType(VectorType::CONSTANT_VECTOR);
  output.SetValue(0, Value("OR-Tools 9.15.6755 / DuckDB ortools 0.1.0"));
}

struct AllocateBindData : TableFunctionData {
  string solver = "glop";
  int64_t time_ms = 10000;
};
struct Edge {
  string worker, bucket;
  double capacity, demand, cost;
};
struct Problem {
  std::map<string, double> capacities, demands;
  std::set<std::pair<string, string>> pairs;
  vector<Edge> edges;
};
struct AllocateState : LocalTableFunctionState {
  std::map<string, Problem> problems;
  vector<std::pair<string, Problem *>> ordered;
  idx_t problem_index = 0, edge_index = 0, bytes = 0, edge_count = 0;
  bool initialized = false, solved = false;
  SolverResult result;
};
static unique_ptr<FunctionData> AllocateBind(ClientContext &context, TableFunctionBindInput &input,
                                             vector<LogicalType> &types, vector<SqlIdentifier> &names) {
#ifdef ORTOOLS_DUCKDB_2
  if (!Settings::Get<EnableOptimizerSetting>(context))
#else
  if (!ClientConfig::GetConfig(context).enable_optimizer)
#endif
    throw BinderException("ortools_allocate requires the optimizer for its input materialization barrier");
  const vector<SqlIdentifier> expected{"scenario_id",     "tasker_id",    "bucket_id",
                                "available_hours", "demand_hours", "gap_cost_per_hour"};
  if (input.input_table_names != expected)
    throw BinderException("ortools_allocate expects columns: scenario_id, tasker_id, bucket_id, available_hours, "
                          "demand_hours, gap_cost_per_hour (in that order)");
  for (idx_t i = 0; i < 6; i++) {
    LogicalType expected_type = i < 3 ? LogicalType::VARCHAR : LogicalType::DOUBLE;
    if (input.input_table_types[i] != expected_type)
      throw BinderException("ortools_allocate: cast %s to %s", expected[i], expected_type.ToString());
  }
  auto data = make_uniq<AllocateBindData>();
  for (const auto &entry : input.named_parameters) {
    if (entry.second.IsNull())
      throw BinderException("ortools_allocate options cannot be NULL");
    if (entry.first == "solver")
      data->solver = entry.second.GetValue<string>();
    if (entry.first == "time_limit_ms")
      data->time_ms = entry.second.GetValue<int64_t>();
  }
  if (data->solver != "cp_sat" && data->solver != "glop")
    throw BinderException("solver must be cp_sat or glop");
  if (data->time_ms < 1 || data->time_ms > 60000)
    throw BinderException("time_limit_ms must be in [1,60000]");
  types = input.input_table_types;
  names = expected;
  for (const auto &column : child_list_t<LogicalType>{{"allocated_hours", LogicalType::DOUBLE},
                                                      {"status", LogicalType::VARCHAR},
                                                      {"objective", LogicalType::DOUBLE},
                                                      {"best_bound", LogicalType::DOUBLE},
                                                      {"wall_time_ms", LogicalType::DOUBLE}}) {
    names.push_back(column.first);
    types.push_back(column.second);
  }
  return std::move(data);
}
static unique_ptr<GlobalTableFunctionState> AllocateGlobal(ClientContext &, TableFunctionInitInput &) {
  return make_uniq<GlobalTableFunctionState>(); // Exactly one producer owns all chunks of each problem.
}
static unique_ptr<LocalTableFunctionState> AllocateLocal(ExecutionContext &, TableFunctionInitInput &,
                                                         GlobalTableFunctionState *) {
  return make_uniq<AllocateState>();
}
static OperatorResultType AllocateInput(ExecutionContext &, TableFunctionInput &data, DataChunk &input, DataChunk &) {
  auto &state = data.local_state->Cast<AllocateState>();
  for (idx_t row = 0; row < input.size(); row++) {
    for (idx_t col = 0; col < 6; col++)
      if (input.GetValue(col, row).IsNull())
        throw InvalidInputException("ortools_allocate rejects NULL inputs");
    const string scenario = input.GetValue(0, row).GetValue<string>();
    Edge edge{input.GetValue(1, row).GetValue<string>(), input.GetValue(2, row).GetValue<string>(),
              input.GetValue(3, row).GetValue<double>(), input.GetValue(4, row).GetValue<double>(),
              input.GetValue(5, row).GetValue<double>()};
    for (const auto &id : {scenario, edge.worker, edge.bucket})
      if (id.empty() || id.size() > 1024)
        throw InvalidInputException("ortools_allocate IDs must contain 1–1024 bytes");
    for (double number : {edge.capacity, edge.demand, edge.cost})
      if (!std::isfinite(number) || std::abs(number) > 9007199254740991.0)
        throw InvalidInputException("ortools_allocate requires finite numeric inputs within ±(2^53-1)");
    if (edge.capacity < 0 || edge.demand < 0)
      throw InvalidInputException("ortools_allocate capacity and demand must be nonnegative");
    state.bytes += scenario.size() + edge.worker.size() + edge.bucket.size() + sizeof(Edge) + 256;
    if (++state.edge_count > 100000 || state.bytes > MAX_BYTES)
      throw InvalidInputException("ortools_allocate input exceeds 100000 edges or 64 MiB; solve smaller scopes");
    auto &problem = state.problems[scenario];
    if (!problem.pairs.emplace(edge.worker, edge.bucket).second)
      throw InvalidInputException("ortools_allocate: duplicate tasker–bucket edge in scenario %s", scenario);
    auto capacity = problem.capacities.emplace(edge.worker, edge.capacity);
    auto demand = problem.demands.emplace(edge.bucket, edge.demand);
    if (capacity.first->second != edge.capacity || demand.first->second != edge.demand)
      throw InvalidInputException(
          "ortools_allocate: inconsistent repeated tasker capacity or bucket demand in scenario %s", scenario);
    problem.edges.push_back(std::move(edge));
  }
  return OperatorResultType::NEED_MORE_INPUT;
}
static Json AllocationModel(const Problem &problem, const AllocateBindData &options) {
  Json model{{"version", 1},
             {"solver", options.solver},
             {"maximize", true},
             {"time_limit_ms", options.time_ms},
             {"variables", Json::array()},
             {"constraints", Json::array()}};
  std::map<string, Json> capacities, demands;
  for (const auto &[id, value] : problem.capacities)
    capacities[id] = {
        {"id", "capacity:" + std::to_string(capacities.size())}, {"upper", value}, {"terms", Json::array()}};
  for (const auto &[id, value] : problem.demands)
    demands[id] = {{"id", "demand:" + std::to_string(demands.size())}, {"upper", value}, {"terms", Json::array()}};
  for (idx_t i = 0; i < problem.edges.size(); i++) {
    const auto &edge = problem.edges[i];
    string id = "edge:" + std::to_string(i);
    model["variables"].push_back({{"id", id},
                                  {"lower", 0},
                                  {"upper", std::min(edge.capacity, edge.demand)},
                                  {"objective", edge.cost},
                                  {"integer", options.solver == "cp_sat"}});
    Json term{{"variable", id}, {"coefficient", 1}};
    capacities[edge.worker]["terms"].push_back(term);
    demands[edge.bucket]["terms"].push_back(term);
  }
  for (const auto &[_, c] : capacities)
    model["constraints"].push_back(c);
  for (const auto &[_, c] : demands)
    model["constraints"].push_back(c);
  return model;
}
static OperatorFinalizeResultType AllocateFinal(ExecutionContext &context, TableFunctionInput &data,
                                                DataChunk &output) {
  auto &state = data.local_state->Cast<AllocateState>();
  auto &options = data.bind_data->Cast<AllocateBindData>();
  if (!state.initialized) {
    for (auto &[id, problem] : state.problems)
      state.ordered.emplace_back(id, &problem);
    state.initialized = true;
  }
  idx_t count = 0;
  while (state.problem_index < state.ordered.size() && count < STANDARD_VECTOR_SIZE) {
    const auto &[scenario, ptr] = state.ordered[state.problem_index];
    auto &problem = *ptr;
    if (!state.solved) {
      state.result = SolveModel(context.client, AllocationModel(problem, options));
      state.solved = true;
    }
    const auto &r = state.result;
    const auto &edge = problem.edges[state.edge_index];
    vector<Value> row{Value(scenario),
                      Value(edge.worker),
                      Value(edge.bucket),
                      Value::DOUBLE(edge.capacity),
                      Value::DOUBLE(edge.demand),
                      Value::DOUBLE(edge.cost),
                      r.values.empty() ? Value(LogicalType::DOUBLE)
                                       : Value::DOUBLE(r.values.at("edge:" + std::to_string(state.edge_index))),
                      Value(r.status),
                      r.objective ? Value::DOUBLE(*r.objective) : Value(LogicalType::DOUBLE),
                      r.best_bound ? Value::DOUBLE(*r.best_bound) : Value(LogicalType::DOUBLE),
                      Value::DOUBLE(r.wall_time_ms)};
    for (idx_t i = 0; i < row.size(); i++)
      output.SetValue(i, count, row[i]);
    count++;
    if (++state.edge_index == problem.edges.size()) {
      state.edge_index = 0;
      state.problem_index++;
      state.solved = false;
    }
  }
  output.SetCardinality(count);
  return state.problem_index == state.ordered.size() ? OperatorFinalizeResultType::FINISHED
                                                     : OperatorFinalizeResultType::HAVE_MORE_OUTPUT;
}
// An in/out function's final callback runs per pipeline. UNION ALL can create
// multiple pipelines even with MaxThreads=1. Insert a blocking sort after the
// built-in optimizers so every producer drains before the single solve consumer.
static void MaterializeAllocation(OptimizerExtensionInput &input, unique_ptr<LogicalOperator> &plan) {
  for (auto &child : plan->children)
    MaterializeAllocation(input, child);
  if (plan->type != LogicalOperatorType::LOGICAL_GET)
    return;
  auto &get = plan->Cast<LogicalGet>();
#ifdef ORTOOLS_DUCKDB_2
  const auto &name = get.function.GetName();
#else
  const auto &name = get.function.name;
#endif
  if (name != "ortools_allocate" || get.children.size() != 1)
    return;
  auto &child = get.children[0];
  auto bindings = child->GetColumnBindings();
  vector<BoundOrderByNode> orders;
  orders.emplace_back(OrderType::ASCENDING, OrderByNullType::NULLS_LAST,
                      make_uniq<BoundColumnRefExpression>(LogicalType::VARCHAR, bindings[0]));
  auto barrier = make_uniq<LogicalOrder>(std::move(orders));
  barrier->children.push_back(std::move(child));
  child = std::move(barrier);
}
static void LoadInternal(ExtensionLoader &loader) {
  ScalarFunction solve("ortools_solve", {LogicalType::VARCHAR}, SolverResultType(), SolveScalar);
#ifdef ORTOOLS_DUCKDB_2
  solve.SetStability(FunctionStability::VOLATILE);
  solve.SetNullHandling(FunctionNullHandling::SPECIAL_HANDLING);
  solve.SetFallible();
#else
  solve.stability = FunctionStability::VOLATILE;
  solve.null_handling = FunctionNullHandling::SPECIAL_HANDLING;
#endif
  loader.RegisterFunction(solve);
  loader.RegisterFunction(ScalarFunction("ortools_version", {}, LogicalType::VARCHAR, VersionScalar));
  TableFunction allocate("ortools_allocate", {LogicalType::TABLE}, nullptr, AllocateBind, AllocateGlobal,
                         AllocateLocal);
#ifdef ORTOOLS_DUCKDB_2
  allocate.GetSignature().WithTypedKwargs("options", [](TypedKwargs &options) {
    options.Add("solver", LogicalType::VARCHAR);
    options.Add("time_limit_ms", LogicalType::BIGINT);
  });
#else
  allocate.named_parameters["solver"] = LogicalType::VARCHAR;
  allocate.named_parameters["time_limit_ms"] = LogicalType::BIGINT;
#endif
  allocate.in_out_function = AllocateInput;
  allocate.in_out_function_final = AllocateFinal;
  loader.RegisterFunction(allocate);
  OptimizerExtension optimizer;
  optimizer.optimize_function = MaterializeAllocation;
#ifdef ORTOOLS_DUCKDB_1_4
  DBConfig::GetConfig(loader.GetDatabaseInstance()).optimizer_extensions.push_back(std::move(optimizer));
#else
  OptimizerExtension::Register(DBConfig::GetConfig(loader.GetDatabaseInstance()), std::move(optimizer));
#endif
}
void OrtoolsExtension::Load(ExtensionLoader &loader) { LoadInternal(loader); }
std::string OrtoolsExtension::Name() { return "ortools"; }
std::string OrtoolsExtension::Version() const { return "0.1.0"; }
} // namespace duckdb
extern "C" {
DUCKDB_CPP_EXTENSION_ENTRY(ortools, loader) { duckdb::LoadInternal(loader); }
}
