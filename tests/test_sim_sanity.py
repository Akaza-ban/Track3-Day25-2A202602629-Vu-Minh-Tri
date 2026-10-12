"""Provided tests — these already pass in the starter repo."""

from __future__ import annotations

import pytest

from gameday_lab.naive_client import NaiveClient
from gameday_lab.sim.config import load_config
from gameday_lab.sim.contracts import Action, Answer, CallContext, Serve
from gameday_lab.sim.engine import evaluate_checks, run_scenario
from gameday_lab.workflows.naive_executor import NaiveExecutor
from gameday_lab.workflows.runner import run_workflows

CFG = load_config("configs/gameday.yaml", "configs/scenarios.yaml")
CACHE_CFG = load_config("configs/gameday.yaml", "configs/scenarios_cache.yaml")


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


def test_cache_tape_mixes_faq_and_regular_turns() -> None:
    m = run_scenario(CACHE_CFG, "C1_faq_outage", NaiveClient()).metrics()
    assert 0.5 < m["faq_turns"] / m["turns"] < 0.7
    assert m["cache_hit_ratio"] == 0 and m["faq_availability"] < 0.9  # without a cache the outage hurts


def test_serve_needs_an_answer_produced_by_the_simulator() -> None:
    class MadeUpAnswers(NaiveClient):
        def start(self, call: CallContext, now: float) -> Action:
            if call.request is not None:
                return Serve(Answer(10**9, "v1", ("policy/refund",), now))
            return super().start(call, now)

    with pytest.raises(ValueError):
        run_scenario(CACHE_CFG, "C1_faq_outage", MadeUpAnswers())
