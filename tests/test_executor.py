"""Part D — idempotent, resumable refund workflow."""

from __future__ import annotations

import pytest

from gameday_lab.workflows.executor import WorkflowExecutor, checkpoint_key, idempotency_key
from gameday_lab.workflows.runner import STEPS, RefundRequest, run_workflows
from gameday_lab.workflows.services import DurableStore, FaultProfile, Services, WorkerCrash

REQ = RefundRequest("s00042", "ord_123", 4900, "a@example.com")
NO_FAULTS = FaultProfile(0.0, 0.0, 0.0)


def test_idempotency_key_is_stable_and_step_specific() -> None:
    assert idempotency_key(REQ, "issue_refund") == idempotency_key(REQ, "issue_refund")
    assert idempotency_key(REQ, "issue_refund") != idempotency_key(REQ, "send_email")
    other = RefundRequest("s00043", "ord_123", 4900, "a@example.com")
    assert idempotency_key(REQ, "issue_refund") != idempotency_key(other, "issue_refund")


def test_happy_path_runs_every_step_once() -> None:
    services, store = Services.build(1, NO_FAULTS), DurableStore()
    result = WorkflowExecutor(services, store).run(REQ)
    assert result["status"] == "done"
    assert services.payments.executions == 1 and services.email.executions == 1
    assert all(store.get(checkpoint_key(REQ, s)) is not None for s in STEPS)


def test_resumes_from_checkpoints() -> None:
    services, store = Services.build(1, NO_FAULTS), DurableStore()
    WorkflowExecutor(services, store).run(REQ)
    llm_before, pay_before = services.llm.executions, services.payments.executions
    WorkflowExecutor(services, store).run(REQ)  # a new worker picks up the same session
    assert services.llm.executions == llm_before  # nothing recomputed
    assert services.payments.executions == pay_before


def test_crash_after_payment_does_not_pay_twice() -> None:
    services, store = Services.build(1, NO_FAULTS), DurableStore()
    services.payments.faults = FaultProfile(0.0, 0.0, 1.0)  # every refund: executed, then the worker dies
    with pytest.raises(WorkerCrash):
        WorkflowExecutor(services, store).run(REQ)
    services.payments.faults = NO_FAULTS
    WorkflowExecutor(services, store).run(REQ)  # restarted worker
    assert len(services.payments.ledger) == 1


def test_lost_response_is_retried_with_the_same_key() -> None:
    services, store = Services.build(1, NO_FAULTS), DurableStore()
    services.payments.faults = FaultProfile(0.0, 1.0, 0.0)  # executed, response lost
    calls = {"n": 0}
    original = services.payments._call

    def flaky_once(payload, key, op):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:
            services.payments.faults = NO_FAULTS
        return original(payload, key, op)

    services.payments._call = flaky_once  # type: ignore[method-assign]
    WorkflowExecutor(services, store).run(REQ)
    assert len(services.payments.ledger) == 1


def test_worker_crash_is_not_swallowed() -> None:
    services, store = Services.build(1, NO_FAULTS), DurableStore()
    services.email.faults = FaultProfile(0.0, 0.0, 1.0)
    with pytest.raises(WorkerCrash):
        WorkflowExecutor(services, store).run(REQ)


def test_500_sessions_exactly_once() -> None:
    m = run_workflows(WorkflowExecutor, n=500, seed=11)
    assert m["duplicate_refunds"] == 0 and m["duplicate_emails"] == 0
    assert m["lost_refunds"] == 0 and m["completion_rate"] >= 0.99
    assert m["wasted_llm_per_crash"] <= 0.6
