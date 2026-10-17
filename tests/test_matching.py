import importlib.util
from pathlib import Path


def test_two_week_matching_and_portfolio_conservation(connection):
    path = Path(__file__).with_name("matching_models.py")
    spec = importlib.util.spec_from_file_location("matching_models", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.run(connection)
