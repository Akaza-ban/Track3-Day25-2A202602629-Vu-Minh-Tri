"""Simulated external services for the refund agent. DO NOT EDIT.

They behave like real payment / e-mail APIs:

* ``ServiceError``   — failed *before* doing anything. Safe to retry.
* ``ServiceTimeout`` — no answer in time. The operation MAY have happened.
* ``WorkerCrash``    — our worker process died while waiting (e.g. OOM-kill,
                       deploy, spot instance reclaimed). Do NOT catch it: the
                       runner will start a fresh worker that must resume.

Side-effecting endpoints accept an ``idempotency_key`` with Stripe-like
semantics: the first request with a key is executed and its result stored;
any later request with the same key returns the stored result without
executing again. Reusing a key with a *different* payload is an error.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class ServiceError(Exception):
    """5xx before the operation started. Safe to retry."""


class ServiceTimeout(Exception):
    """No response. The operation may or may not have been executed."""


class WorkerCrash(BaseException):
    """The worker process died. Inherits BaseException so a bare
    ``except Exception`` does not swallow it — don't catch it either."""


class IdempotencyConflict(Exception):
    """Same idempotency key, different payload."""


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass
class FaultProfile:
    error_before: float = 0.05  # ServiceError, nothing executed
    timeout_after_commit: float = 0.10  # executed, but the response is lost
    crash_after_commit: float = 0.04  # executed, then our worker dies


@dataclass
class _FlakyService:
    name: str
    rng: random.Random
    faults: FaultProfile
    executions: int = 0  # how many times the operation really ran
    requests: int = 0
    _idem: dict[str, tuple[str, Any]] = field(default_factory=dict)

    def _call(self, payload: dict[str, Any], key: str | None, op: Callable[[], Any]) -> Any:
        self.requests += 1
        if key is not None and key in self._idem:
            fp, result = self._idem[key]
            if fp != _fingerprint(payload):
                raise IdempotencyConflict(f"{self.name}: key {key!r} reused with a different payload")
            return result
        r = self.rng.random()
        if r < self.faults.error_before:
            raise ServiceError(f"{self.name}: 503 before execution")
        result = op()
        self.executions += 1
        if key is not None:
            self._idem[key] = (_fingerprint(payload), result)
        r = self.rng.random()
        if r < self.faults.crash_after_commit:
            raise WorkerCrash(f"{self.name}: worker died after the request was executed")
        if r < self.faults.crash_after_commit + self.faults.timeout_after_commit:
            raise ServiceTimeout(f"{self.name}: timed out (request may have been executed)")
        return result


class PaymentsAPI(_FlakyService):
    def __init__(self, rng: random.Random, faults: FaultProfile) -> None:
        super().__init__("payments", rng, faults)
        self.ledger: list[dict[str, Any]] = []

    def refund(self, order_id: str, amount_cents: int, idempotency_key: str | None = None) -> dict[str, Any]:
        payload = {"order_id": order_id, "amount_cents": amount_cents}

        def op() -> dict[str, Any]:
            refund = {"refund_id": f"re_{len(self.ledger) + 1:06d}", **payload}
            self.ledger.append(refund)
            return refund

        return dict(self._call(payload, idempotency_key, op))


class EmailAPI(_FlakyService):
    def __init__(self, rng: random.Random, faults: FaultProfile) -> None:
        super().__init__("email", rng, faults)
        self.outbox: list[dict[str, Any]] = []

    def send(self, to: str, template: str, data: dict[str, Any],
             idempotency_key: str | None = None) -> dict[str, Any]:
        payload = {"to": to, "template": template, "data": data}

        def op() -> dict[str, Any]:
            msg = {"message_id": f"msg_{len(self.outbox) + 1:06d}", **payload}
            self.outbox.append(msg)
            return msg

        return dict(self._call(payload, idempotency_key, op))


class OrdersAPI(_FlakyService):
    """Read-only. Retrying is always safe; no idempotency key needed."""

    def __init__(self, rng: random.Random, faults: FaultProfile) -> None:
        super().__init__("orders", rng, faults)

    def get(self, order_id: str) -> dict[str, Any]:
        return dict(self._call({"order_id": order_id}, None,
                               lambda: {"order_id": order_id, "status": "delivered", "refundable": True}))


class LLM(_FlakyService):
    """Pure computation (no side effect) — just costs money when repeated."""

    def __init__(self, rng: random.Random, faults: FaultProfile) -> None:
        super().__init__("llm", rng, faults)

    def classify(self, order_id: str, amount_cents: int) -> dict[str, Any]:
        return dict(self._call({"o": order_id, "a": amount_cents}, None,
                               lambda: {"intent": "refund", "order_id": order_id, "amount_cents": amount_cents}))

    def summarize(self, refund_id: str, message_id: str) -> str:
        return str(self._call({"r": refund_id, "m": message_id}, None,
                              lambda: f"Refund {refund_id} issued, confirmation {message_id} sent."))


@dataclass
class Services:
    orders: OrdersAPI
    payments: PaymentsAPI
    email: EmailAPI
    llm: LLM

    @classmethod
    def build(cls, seed: int, faults: FaultProfile | None = None) -> Services:
        f = faults or FaultProfile()
        rng = random.Random(seed)
        # reads / LLM calls: transient errors and crashes, but no lost responses
        read_only = FaultProfile(f.error_before, 0.0, f.crash_after_commit / 2)
        return cls(
            OrdersAPI(random.Random(rng.random()), read_only),
            PaymentsAPI(random.Random(rng.random()), f),
            EmailAPI(random.Random(rng.random()), f),
            LLM(random.Random(rng.random()), read_only),
        )


class DurableStore:
    """A key-value store that survives worker crashes (think Postgres / Redis
    with persistence / LangGraph checkpointer). Only JSON-like values."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self.writes = 0

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key, default)
        return json.loads(json.dumps(value)) if value is not None else default

    def put(self, key: str, value: Any) -> None:
        self.writes += 1
        self._data[key] = json.loads(json.dumps(value))

    def keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]
