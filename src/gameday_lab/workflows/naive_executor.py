"""Baseline executor: retries on errors, but no idempotency keys and no
checkpoints. DO NOT EDIT (used for comparison)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from gameday_lab.workflows.runner import RefundRequest
from gameday_lab.workflows.services import DurableStore, ServiceError, Services, ServiceTimeout

T = TypeVar("T")


class NaiveExecutor:
    def __init__(self, services: Services, store: DurableStore) -> None:
        self.s = services

    def _retry(self, fn: Callable[[], T], attempts: int = 4) -> T:
        for i in range(attempts):
            try:
                return fn()
            except (ServiceError, ServiceTimeout):
                if i == attempts - 1:
                    raise
        raise AssertionError("unreachable")

    def run(self, req: RefundRequest) -> dict[str, Any]:
        intent = self._retry(lambda: self.s.llm.classify(req.order_id, req.amount_cents))
        order = self._retry(lambda: self.s.orders.get(intent["order_id"]))
        refund = self._retry(lambda: self.s.payments.refund(req.order_id, req.amount_cents))
        msg = self._retry(lambda: self.s.email.send(req.customer_email, "refund_done",
                                                    {"refund_id": refund["refund_id"]}))
        summary = self._retry(lambda: self.s.llm.summarize(refund["refund_id"], msg["message_id"]))
        return {"status": "done", "intent": intent, "order": order, "refund": refund, "message": msg, "summary": summary}
