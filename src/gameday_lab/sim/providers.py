"""Provider model. DO NOT EDIT.

Each deployment is a pool of ``servers`` processing requests FIFO. A request
that would wait longer than ``max_queue_wait_s`` is shed with ``529``.
Requests are *not* cancelled when the client times out — the server keeps
working on them. That wasted work is what turns a retry storm into a
metastable failure: the provider stays saturated even after the incident ends.
"""

from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass

from gameday_lab.sim.config import Effect, ProviderSpec
from gameday_lab.sim.contracts import ProviderError

FAST_ERROR_S = 0.05


@dataclass
class Outcome:
    latency_s: float  # math.inf = the provider never answers
    error: ProviderError | None
    bad_output: bool = False  # e.g. tool call answered by a model without tool support
    did_work: bool = False  # the provider spent compute on it (billable)


@dataclass
class _Active:
    capacity_factor: float = 1.0
    extra_latency_s: float = 0.0
    error_rate: float = 0.0
    outage: str | None = None
    quota_exhausted: bool = False
    rate_limit_rps: float | None = None


class ProviderSim:
    def __init__(self, spec: ProviderSpec, effects: tuple[Effect, ...], rng: random.Random) -> None:
        self.spec = spec
        self.effects = tuple(e for e in effects if e.provider in (None, spec.name)
                             and e.family in (None, spec.family))
        self.rng = rng
        self._free: list[float] = [0.0] * spec.servers  # heap of server-free times
        self._rl_window = -1
        self._rl_count = 0

    def _active(self, now: float) -> _Active:
        a = _Active()
        for e in self.effects:
            if not e.applies(self.spec, now):
                continue
            a.capacity_factor = min(a.capacity_factor, e.capacity_factor)
            a.extra_latency_s = max(a.extra_latency_s, e.extra_latency_s)
            a.error_rate = max(a.error_rate, e.error_rate)
            a.outage = e.outage or a.outage
            a.quota_exhausted = a.quota_exhausted or e.quota_exhausted
            if e.rate_limit_rps is not None:
                a.rate_limit_rps = e.rate_limit_rps
        return a

    def _err(self, status: int, code: str, retry_after: float | None = None) -> ProviderError:
        return ProviderError(self.spec.name, status, code, retry_after=retry_after)

    def attempt(self, now: float, task: str, malformed: bool) -> Outcome:
        a = self._active(now)
        if a.outage == "refuse":
            return Outcome(FAST_ERROR_S, self._err(503, "service_unavailable"))
        if a.outage == "hang":
            return Outcome(math.inf, None)
        if a.quota_exhausted:
            return Outcome(FAST_ERROR_S, self._err(429, "insufficient_quota"))

        limit = a.rate_limit_rps if a.rate_limit_rps is not None else self.spec.rate_limit_rps
        if limit is not None:
            window = math.floor(now)
            if window != self._rl_window:
                self._rl_window, self._rl_count = window, 0
            if self._rl_count >= limit:
                retry_after = float(max(1, math.ceil(window + 1 - now)))
                return Outcome(FAST_ERROR_S, self._err(429, "rate_limit_exceeded", retry_after))
            self._rl_count += 1

        if malformed:
            return Outcome(FAST_ERROR_S, self._err(400, "invalid_request_error"))

        free_at = heapq.heappop(self._free)
        start = max(now, free_at)
        wait = start - now
        if wait > self.spec.max_queue_wait_s:
            heapq.heappush(self._free, free_at)
            return Outcome(0.1, self._err(529, "overloaded_error"))
        median = self.spec.service_median_s / max(a.capacity_factor, 1e-6)
        service = median * math.exp(self.rng.gauss(0.0, self.spec.service_sigma))
        heapq.heappush(self._free, start + service)

        if self.rng.random() < self.spec.base_error_rate + a.error_rate:
            return Outcome(wait + 0.3 * service, self._err(500, "api_error"), did_work=True)
        bad = task == "tool" and not self.spec.supports_tools
        return Outcome(wait + service + a.extra_latency_s, None, bad_output=bad, did_work=True)
