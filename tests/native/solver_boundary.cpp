#include "ortools/linear_solver/linear_solver.h"
#include <iostream>
#include <memory>

int main() {
  using operations_research::MPSolver;
  bool forbidden_available = false;
  for (const char *name : {
           "CBC", "CBC_MIXED_INTEGER_PROGRAMMING", "CLP", "CLP_LINEAR_PROGRAMMING",
           "GUROBI_LINEAR_PROGRAMMING", "GUROBI_MIXED_INTEGER_PROGRAMMING",
           "CPLEX_LINEAR_PROGRAMMING", "CPLEX_MIXED_INTEGER_PROGRAMMING",
           "XPRESS_LINEAR_PROGRAMMING", "XPRESS_MIXED_INTEGER_PROGRAMMING",
           "GLPK_LINEAR_PROGRAMMING", "GLPK_MIXED_INTEGER_PROGRAMMING",
           "SCIP_MIXED_INTEGER_PROGRAMMING", "HIGHS_LINEAR_PROGRAMMING",
           "HIGHS_MIXED_INTEGER_PROGRAMMING", "PDLP_LINEAR_PROGRAMMING"}) {
    MPSolver::OptimizationProblemType type;
    if (!MPSolver::ParseSolverType(name, &type)) return 4;
    std::unique_ptr<MPSolver> solver(MPSolver::CreateSolver(name));
    if (solver || MPSolver::SupportsProblemType(type)) {
      std::cerr << "Excluded solver is available: " << name << '\n';
      forbidden_available = true;
    }
  }
  if (forbidden_available) return 1;
  for (const char *name : {"GLOP", "SAT"}) {
    std::unique_ptr<MPSolver> solver(MPSolver::CreateSolver(name));
    if (!solver) return 2;
    auto *x = solver->MakeIntVar(0, 4, "x");
    solver->MutableObjective()->SetCoefficient(x, 2);
    solver->MutableObjective()->SetMaximization();
    if (solver->Solve() != MPSolver::OPTIMAL || solver->Objective().Value() != 8) return 3;
  }
  std::cout << "Excluded solver factories unavailable; GLOP and CP-SAT solve successfully\n";
}
