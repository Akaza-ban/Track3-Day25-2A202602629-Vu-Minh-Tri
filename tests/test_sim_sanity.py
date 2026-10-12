"""Provided tests — these already pass in the starter repo."""

from __future__ import annotations

from gameday_lab.naive_client import NaiveClient
from gameday_lab.sim.config import load_config
from gameday_lab.sim.engine import evaluate_checks, run_scenario
from gameday_lab.workflows.naive_executor import NaiveExecutor
from gameday_lab.workflows.runner import run_workflows

CFG = load_config("configs/gameday.yaml", "configs/scenarios.yaml")


def test_config_has_six_tapes() -> None:
    assert len(CFG.scenarios) == 6
    assert CFG.fallback_order[0] == "alpha"


def test_simulation_is_deterministic() -> None:
    a = run_scenario(CFG, "G1_rate_limit_spike", NaiveClient()).metrics()
    b = run_scenario(CFG, "G1_rate_limit_spike", NaiveClient()).metrics()
    assert a == b


def test_naive_client_causes_a_retry_storm() -> None:
    m = run_scenario(CFG, "G2_overload_storm", NaiveClient()).metrics()
    assert m["amplification"] > 5
    assert not all(evaluate_checks(m, CFG.scenarios["G2_overload_storm"].checks).values())


def test_naive_executor_pays_twice() -> None:
    m = run_workflows(NaiveExecutor, n=200, seed=3)
    assert m["duplicate_refunds"] > 0
