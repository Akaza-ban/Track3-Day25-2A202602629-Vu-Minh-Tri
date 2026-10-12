"""The baseline: what many teams ship first. DO NOT EDIT (used for comparison).

Three retry layers stacked on top of each other, each with a fixed delay:

    app code      retries 3 times  (4 attempts)
    LLM gateway   retries 2 times  (x3)
    provider SDK  retries 2 times  (x3)   -> up to 36 attempts per logical call

No error classification, no jitter, no retry-after, no breaker, no fallback,
no deadline awareness. Every error is "just retry it".
"""

from __future__ import annotations

from gameday_lab.sim.contracts import Action, Attempt, BaseClient, CallContext, GiveUp, ProviderError, Send

LAYERS = (4, 3, 3)
MAX_ATTEMPTS = LAYERS[0] * LAYERS[1] * LAYERS[2]


class NaiveClient(BaseClient):
    name = "naive"

    def __init__(self, primary: str = "alpha", timeout_s: float = 8.0, delay_s: float = 0.2) -> None:
        self.primary = primary
        self.timeout_s = timeout_s
        self.delay_s = delay_s

    def start(self, call: CallContext, now: float) -> Action:
        return Send(self.primary, timeout_s=self.timeout_s)

    def on_success(self, attempt: Attempt, now: float, latency_s: float) -> None:
        return None

    def on_error(self, attempt: Attempt, err: ProviderError, now: float) -> Action:
        if attempt.number >= MAX_ATTEMPTS:
            return GiveUp("retries_exhausted")
        return Send(self.primary, timeout_s=self.timeout_s, delay_s=self.delay_s)
