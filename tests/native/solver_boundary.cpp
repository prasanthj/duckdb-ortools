#include "ortools/linear_solver/linear_solver.h"
#include <iostream>
#include <memory>

int main() {
  using operations_research::MPSolver;
  bool forbidden_available = false;
  for (const char *name : {"CBC", "CBC_MIXED_INTEGER_PROGRAMMING", "CLP", "CLP_LINEAR_PROGRAMMING"}) {
    std::unique_ptr<MPSolver> solver(MPSolver::CreateSolver(name));
    if (solver) {
      auto *x = solver->MakeNumVar(0, 4, "x");
      solver->MutableObjective()->SetCoefficient(x, 2);
      solver->MutableObjective()->SetMaximization();
      std::cerr << "Forbidden EPL solver is callable: " << name << ", status=" << solver->Solve() << '\n';
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
  std::cout << "CBC/CLP factories unavailable; GLOP and CP-SAT solve successfully\n";
}
