"""Shared types between the simulator (provided) and student code.

DO NOT EDIT — the grader uses its own copy of this file.

Time is *virtual*: every method receives ``now`` (seconds since the start of
the scenario). Never call ``time.time()`` or ``time.sleep()`` in student code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """An error returned by a (simulated) LLM provider.

    Mirrors what real SDKs expose:

    * ``status``      HTTP status code. ``0`` means the *client* gave up (timeout).
    * ``code``        machine-readable error code, e.g. ``"rate_limit_exceeded"``,
                      ``"insufficient_quota"``, ``"overloaded_error"``,
                      ``"api_error"``, ``"service_unavailable"``,
                      ``"invalid_request_error"``, ``"client_timeout"``.
    * ``retry_after`` seconds from the ``retry-after`` header, or ``None``.
    """

    def __init__(
        self,
        provider: str,
        status: int,
        code: str,
        message: str = "",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"[{provider}] {status} {code} {message}".strip())
        self.provider = provider
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class ProviderInfo:
    """What the client is allowed to know about a provider deployment."""

    name: str
    family: str  # model family; same family = same model, different deployment
    region: str
    supports_tools: bool
    cost_per_call: float


@dataclass(frozen=True)
class Request:
    """What the user asked — only on FAQ turns (Part G), which may be answered from a cache."""

    text: str
    user_id: str
    personal: bool  # the answer depends on this user's own data (order, refund, points)
    prompt_version: str  # system prompt + model version the answer must be generated with
    embedding: tuple[float, ...]  # unit vector from the (simulated) embedding model


@dataclass(frozen=True)
class Answer:
    """A model answer to a Request. The simulator puts it in ``call.answer`` before ``on_success``."""

    answer_id: int
    prompt_version: str
    sources: tuple[str, ...]  # documents the answer is grounded on, e.g. ("policy/refund",)
    created_at: float


@dataclass
class CallContext:
    """One *logical* LLM call (one step of a user turn)."""

    call_id: int
    turn_id: int
    task: str  # "tool" (needs tool calling) or "chat"
    started_at: float
    deadline: float  # absolute virtual time; the user is gone after this
    state: dict[str, Any] = field(default_factory=dict)  # scratch space for the client
    request: Request | None = None  # FAQ turns only (Part G); None for every other call
    answer: Answer | None = None  # set by the simulator on success, for calls that carry a request


@dataclass(frozen=True)
class Send:
    """Ask the simulator to send one attempt to ``provider`` after ``delay_s``.

    ``tag`` is opaque to the simulator and handed back on the result
    (use it for e.g. a circuit-breaker permit).
    """

    provider: str
    timeout_s: float
    delay_s: float = 0.0
    tag: Any = None


@dataclass(frozen=True)
class GiveUp:
    """Stop trying: the user gets a static, degraded answer."""

    reason: str


@dataclass(frozen=True)
class Serve:
    """Answer the call from your cache instead of a provider (Part G).

    ``answer`` must be an Answer the simulator produced earlier (from ``call.answer``).
    ``stale=True`` marks an answer served past its TTL because no provider could answer.
    """

    answer: Answer
    stale: bool = False


Action = Send | GiveUp | Serve


@dataclass(frozen=True)
class Attempt:
    """One physical attempt, as reported back to the client."""

    call: CallContext
    provider: str
    number: int  # 1-based index of this attempt within the logical call
    sent_at: float
    tag: Any


class BaseClient:
    """Interface every client implements. The simulator drives it with events."""

    name = "base"

    def start(self, call: CallContext, now: float) -> Action:
        """A new logical call begins. Return the first action."""
        raise NotImplementedError

    def on_success(self, attempt: Attempt, now: float, latency_s: float) -> None:
        """An attempt succeeded. The logical call is finished."""
        raise NotImplementedError

    def on_error(self, attempt: Attempt, err: ProviderError, now: float) -> Action:
        """An attempt failed. Return the next action (retry, failover or give up)."""
        raise NotImplementedError

    def on_cancel(self, call: CallContext, tag: Any, now: float) -> None:
        """A delayed attempt was dropped because the call's deadline passed.

        Release anything you reserved for it (e.g. a half-open probe slot).
        """
        return

    def on_content_update(self, source: str, now: float) -> None:
        """A document that answers are grounded on has changed (CMS webhook, Part G).

        Answers built from the old version of ``source`` are now wrong.
        """
        return
