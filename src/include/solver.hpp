#pragma once
#include "duckdb.hpp"
#include "json.hpp"
#include <map>
#include <optional>

namespace duckdb {
using Json = nlohmann::json;
struct SolverResult {
  string status;
  string solver;
  string message;
  double wall_time_ms = 0;
  std::optional<double> objective;
  std::optional<double> best_bound;
  std::map<string, double> values;
  vector<string> conflicts;
};
SolverResult SolveModel(ClientContext &context, const Json &document);
LogicalType SolverResultType();
Value SolverResultValue(const SolverResult &result);
bool SolverInterrupted(ClientContext &context);
} // namespace duckdb
