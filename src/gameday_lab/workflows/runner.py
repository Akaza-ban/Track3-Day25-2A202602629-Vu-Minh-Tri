"""Runs many refund workflows against flaky services, restarting crashed
workers — like Kubernetes would. DO NOT EDIT."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from gameday_lab.workflows.services import DurableStore, FaultProfile, Services, WorkerCrash

STEPS = ("classify_request", "lookup_order", "issue_refund", "send_email", "write_summary")
MAX_RESTARTS = 10


@dataclass(frozen=True)
class RefundRequest:
    session_id: str
    order_id: str
    amount_cents: int
    customer_email: str


class Executor(Protocol):
    def run(self, req: RefundRequest) -> dict[str, Any]: ...


class ExecutorFactory(Protocol):
    def __call__(self, services: Services, store: DurableStore) -> Executor: ...


def make_requests(n: int, seed: int) -> list[RefundRequest]:
    rng = random.Random(seed)
    return [
        RefundRequest(f"s{i:05d}", f"ord_{rng.randint(10**6, 10**7)}", rng.choice([1500, 4900, 12000, 35000]),
                      f"user{i}@example.com")
        for i in range(n)
    ]


def run_workflows(factory: ExecutorFactory, n: int = 500, seed: int = 25,
                  faults: FaultProfile | None = None) -> dict[str, Any]:
    services = Services.build(seed, faults)
    store = DurableStore()  # survives crashes, shared by all workers
    requests = make_requests(n, seed)
    completed = 0
    crashes = 0
    failed: Counter[str] = Counter()
    for req in requests:
        for _restart in range(MAX_RESTARTS + 1):
            worker = factory(services, store)  # a brand-new process: no memory
            try:
                result = worker.run(req)
            except WorkerCrash:
                crashes += 1
                continue
            except NotImplementedError:
                raise
            except Exception as exc:  # noqa: BLE001 — workflow gave up
                failed[type(exc).__name__] += 1
                break
            if result.get("status") == "done":
                completed += 1
            else:
                failed[str(result.get("status"))] += 1
            break
        else:
            failed["too_many_restarts"] += 1

    refunds_per_order = Counter(r["order_id"] for r in services.payments.ledger)
    emails_per_user = Counter(m["to"] for m in services.email.outbox)
    duplicate_refunds = sum(c - 1 for c in refunds_per_order.values() if c > 1)
    duplicate_emails = sum(c - 1 for c in emails_per_user.values() if c > 1)
    refunded_orders = set(refunds_per_order)
    lost_refunds = sum(1 for r in requests if r.order_id not in refunded_orders)
    money_out = sum(r["amount_cents"] for r in services.payments.ledger)
    money_owed = sum(r.amount_cents for r in requests)
    wasted_llm = services.llm.executions - 2 * n  # classify + summarize once per session is the minimum
    return {
        "sessions": n,
        "completed": completed,
        "completion_rate": round(completed / n, 4),
        "worker_crashes": crashes,
        "failed": dict(failed),
        "duplicate_refunds": duplicate_refunds,
        "duplicate_emails": duplicate_emails,
        "lost_refunds": lost_refunds,
        "overpaid_cents": money_out - money_owed if money_out > money_owed else 0,
        "payments_executions": services.payments.executions,
        "wasted_llm_calls": wasted_llm,
        "wasted_llm_per_crash": round(wasted_llm / crashes, 3) if crashes else 0.0,
        "store_writes": store.writes,
    }
