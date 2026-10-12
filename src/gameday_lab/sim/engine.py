"""Discrete-event simulator with a virtual clock. DO NOT EDIT.

A scenario of 8 virtual minutes with thousands of concurrent user turns runs
in a few seconds and is fully reproducible for a given seed.
"""

from __future__ import annotations

import heapq
import itertools
import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from gameday_lab.sim.config import LabConfig, Scenario
from gameday_lab.sim.contracts import (
    Answer,
    Attempt,
    BaseClient,
    CallContext,
    GiveUp,
    ProviderError,
    Request,
    Send,
    Serve,
)
from gameday_lab.sim.faq import FaqCatalog, Intent
from gameday_lab.sim.providers import Outcome, ProviderSim

MAX_ATTEMPTS_PER_CALL = 100  # safety net against infinite retry loops
TAIL_S = 120.0  # keep processing in-flight work this long after the last arrival
CACHE_LATENCY_S = 0.02  # answering from the cache


@dataclass
class _Turn:
    turn_id: int
    start: float
    deadline: float
    steps: tuple[str, ...] = ("tool", "chat")
    request: Request | None = None  # FAQ turns (Part G)
    intent: Intent | None = None  # ground truth for the request, hidden from the client
    step: int = 0
    end: float | None = None
    good: bool = False


@dataclass(frozen=True)
class _AnswerTruth:
    answer: Answer
    intent: str
    owner: str | None  # user the answer belongs to (personal questions)
    versions: tuple[int, ...]  # version of each source document when the answer was generated


@dataclass
class _CallMeta:
    turn: _Turn
    task: str
    malformed: bool
    attempts: int = 0
    had_400: bool = False
    not_before: dict[str, float] = field(default_factory=dict)
    providers: list[str] = field(default_factory=list)


class Simulation:
    def __init__(self, cfg: LabConfig, scenario: Scenario, client: BaseClient, seed: int | None = None) -> None:
        self.cfg = cfg
        self.sc = scenario
        self.client = client
        base_seed = cfg.seed if seed is None else seed
        self.rng_arrivals = random.Random(f"{base_seed}-{scenario.key}-arrivals")
        self.rng_calls = random.Random(f"{base_seed}-{scenario.key}-calls")
        self.providers = {
            p.name: ProviderSim(p, scenario.effects, random.Random(f"{base_seed}-{scenario.key}-{p.name}"))
            for p in cfg.providers
        }
        self.primary = cfg.fallback_order[0]
        self.infos = cfg.provider_infos()
        # --- Part G: FAQ traffic, documents, prompt versions, answers ---
        self.rng_faq = random.Random(f"{base_seed}-{scenario.key}-faq")
        self.faq = FaqCatalog() if scenario.faq is not None else None
        self.prompt_version = "v1"
        self.doc_version: Counter[str] = Counter()
        self.announced_version: Counter[str] = Counter()  # last version the client was told about
        self.answers: dict[int, _AnswerTruth] = {}
        self._answer_ids = itertools.count(1)
        self.cache_served = 0
        self.stale_served = 0
        self.wrong_answers: Counter[str] = Counter()
        self.outdated_answers = 0
        self.per_second_cache: Counter[int] = Counter()
        self.now = 0.0
        self._heap: list[tuple[float, int, Callable[..., None], tuple[Any, ...]]] = []
        self._seq = itertools.count()
        self._call_ids = itertools.count(1)
        self.meta: dict[int, _CallMeta] = {}
        # --- recorded data ---
        self.turns: list[_Turn] = []
        self.calls_total = 0
        self.calls_ok = 0
        self.attempts_by_provider: Counter[str] = Counter()
        self.errors_by_code: Counter[str] = Counter()
        self.give_up_reasons: Counter[str] = Counter()
        self.cost = 0.0
        self.retry_after_violations = 0
        self.same_provider_retries = 0
        self.client_error_retries = 0
        self.incompatible_routes = 0
        self.unneeded_failovers = 0
        self.primary_only_calls = 0
        self.per_second_attempts: dict[int, Counter[str]] = defaultdict(Counter)
        self.per_second_calls: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # [ok, total]

    # ------------------------------------------------------------------ events
    def _at(self, t: float, fn: Callable[..., None], *args: Any) -> None:
        heapq.heappush(self._heap, (t, next(self._seq), fn, args))

    def run(self) -> Simulation:
        wl = self.cfg.workload
        t = 0.0
        turn_id = 0
        while True:
            mult = next((w.value for w in self.sc.traffic if w.contains(t)), 1.0)
            t += self.rng_arrivals.expovariate(wl.turns_per_s * mult)
            if t >= self.sc.duration_s:
                break
            turn_id += 1
            self._at(t, self._new_turn, turn_id)
        for u in self.sc.content_updates:
            self._at(u.t, self._update_document, u.value, True)
        for u in self.sc.silent_updates:
            self._at(u.t, self._update_document, u.value, False)
        for d in self.sc.prompt_deploys:
            self._at(d.t, self._deploy_prompt, d.value)
        horizon = self.sc.duration_s + TAIL_S
        while self._heap:
            t, _, fn, args = heapq.heappop(self._heap)
            if t > horizon:
                break
            self.now = t
            fn(*args)
        return self

    # ------------------------------------------------------------------ turns
    def _new_turn(self, turn_id: int) -> None:
        turn = _Turn(turn_id, self.now, self.now + self.cfg.workload.turn_deadline_s,
                     steps=self.cfg.workload.steps)
        if self.faq is not None and self.sc.faq is not None and self.rng_faq.random() < self.sc.faq.share:
            turn.request, turn.intent = self.faq.sample(self.rng_faq, self.sc.faq, self.prompt_version)
            turn.steps = ("chat",)  # FAQ: answered from the policy documents, no tool call
        self.turns.append(turn)
        self._start_call(turn)

    def _update_document(self, source: str, announced: bool) -> None:
        self.doc_version[source] += 1
        if announced:
            self.announced_version[source] = self.doc_version[source]
            self.client.on_content_update(source, self.now)

    def _deploy_prompt(self, version: str) -> None:
        self.prompt_version = version

    def _malformed_rate(self, t: float) -> float:
        rate = self.cfg.workload.base_malformed_rate
        for w in self.sc.malformed:
            if w.contains(t):
                rate = max(rate, w.value)
        return rate

    def _start_call(self, turn: _Turn) -> None:
        task = turn.steps[turn.step]
        call = CallContext(next(self._call_ids), turn.turn_id, task, self.now, turn.deadline,
                           request=turn.request)
        meta = _CallMeta(turn, task, self.rng_calls.random() < self._malformed_rate(self.now))
        self.meta[call.call_id] = meta
        self.calls_total += 1
        self._dispatch(call, self.client.start(call, self.now))

    def _finish_call(self, call: CallContext, ok: bool, reason: str = "") -> None:
        meta = self.meta.pop(call.call_id)
        sec = int(self.now)
        self.per_second_calls[sec][1] += 1
        if ok:
            self.calls_ok += 1
            self.per_second_calls[sec][0] += 1
        else:
            self.give_up_reasons[reason or "unknown"] += 1
        turn = meta.turn
        if not ok or self.now > turn.deadline:
            turn.end, turn.good = self.now, False
            return
        turn.step += 1
        if turn.step >= len(turn.steps):
            turn.end, turn.good = self.now, True
            return
        gap = self.cfg.workload.tool_exec_s if meta.task == "tool" else 0.0
        self._at(self.now + gap, self._start_call, turn)

    # ------------------------------------------------------------------ attempts
    def _dispatch(self, call: CallContext, action: Any) -> None:
        if isinstance(action, GiveUp):
            self._finish_call(call, False, action.reason)
            return
        if isinstance(action, Serve):
            self._serve(call, action)
            return
        if not isinstance(action, Send):
            raise TypeError(f"client returned {action!r}; expected Send, GiveUp or Serve")
        if action.provider not in self.providers:
            raise ValueError(f"unknown provider {action.provider!r}")
        meta = self.meta[call.call_id]
        if meta.attempts >= MAX_ATTEMPTS_PER_CALL:
            self.client.on_cancel(call, action.tag, self.now)
            self._finish_call(call, False, "engine_attempt_limit")
            return
        self._at(self.now + max(0.0, action.delay_s), self._send, call, action)

    def _send(self, call: CallContext, send: Send) -> None:
        meta = self.meta[call.call_id]
        if self.now >= call.deadline:
            self.client.on_cancel(call, send.tag, self.now)
            self._finish_call(call, False, "deadline")
            return
        p = send.provider
        if meta.providers and meta.providers[-1] == p:
            self.same_provider_retries += 1
        meta.attempts += 1
        meta.providers.append(p)
        self.attempts_by_provider[p] += 1
        self.per_second_attempts[int(self.now)][p] += 1
        if self.now < meta.not_before.get(p, 0.0) - 1e-9:
            self.retry_after_violations += 1
        if meta.had_400:
            self.client_error_retries += 1
        if p != self.primary and not meta.malformed and any(w.contains(self.now) for w in self.sc.primary_only):
            self.unneeded_failovers += 1

        out: Outcome = self.providers[p].attempt(self.now, meta.task, meta.malformed)
        latency, err = out.latency_s, out.error
        if latency > send.timeout_s:
            latency, err = send.timeout_s, ProviderError(p, 0, "client_timeout")
        attempt = Attempt(call, p, meta.attempts, self.now, send.tag)
        self._at(self.now + latency, self._on_result, attempt, err, latency, out)

    def _on_result(self, attempt: Attempt, err: ProviderError | None, latency: float, out: Outcome) -> None:
        call = attempt.call
        meta = self.meta[call.call_id]
        if out.did_work:
            self.cost += self.infos[attempt.provider].cost_per_call
        if err is not None:
            self.errors_by_code[err.code] += 1
            if err.status == 400:
                meta.had_400 = True
            if err.retry_after is not None:
                meta.not_before[attempt.provider] = self.now + err.retry_after
            self._dispatch(call, self.client.on_error(attempt, err, self.now))
            return
        if meta.turn.request is not None and not out.bad_output:
            call.answer = self._new_answer(meta.turn)
        self.client.on_success(attempt, self.now, latency)
        if out.bad_output:
            self.incompatible_routes += 1
            self._finish_call(call, False, "incompatible_model_output")
        else:
            self._finish_call(call, True)

    # ------------------------------------------------------------------ cache (Part G)
    def _new_answer(self, turn: _Turn) -> Answer:
        assert self.faq is not None and turn.request is not None and turn.intent is not None
        req = turn.request
        sources = self.faq.sources(turn.intent, req.user_id)
        answer = Answer(next(self._answer_ids), req.prompt_version, sources, self.now)
        owner = req.user_id if req.personal else None
        self.answers[answer.answer_id] = _AnswerTruth(answer, turn.intent.key, owner,
                                                      tuple(self.doc_version[s] for s in sources))
        return answer

    def _serve(self, call: CallContext, serve: Serve) -> None:
        turn = self.meta[call.call_id].turn
        if turn.request is None or turn.intent is None:
            raise ValueError("Serve is only allowed on calls that carry a request (FAQ turns)")
        truth = self.answers.get(serve.answer.answer_id)
        if truth is None or truth.answer != serve.answer:
            raise ValueError("Serve must return an Answer produced by the simulator (call.answer)")
        self.cache_served += 1
        self.stale_served += int(serve.stale)
        self.per_second_cache[int(self.now)] += 1
        reason = self._wrong_reason(turn, truth)
        if reason is not None:
            self.wrong_answers[reason] += 1
        elif any(self.doc_version[s] != v for s, v in zip(truth.answer.sources, truth.versions)):
            self.outdated_answers += 1  # nobody announced the change: only a TTL limits this
        ok = reason is None
        self._at(self.now + CACHE_LATENCY_S, self._finish_call, call, ok, "" if ok else f"cache_{reason}")

    def _wrong_reason(self, turn: _Turn, truth: _AnswerTruth) -> str | None:
        req = turn.request
        assert req is not None and turn.intent is not None
        if truth.owner is not None and truth.owner != req.user_id:
            return "other_user"  # data leak: someone else's order / refund / points
        if truth.intent != turn.intent.key:
            return "other_question"  # false hit
        if req.personal:
            return "personal_cached"  # personal answers go stale in minutes — never cache them
        if truth.answer.prompt_version != req.prompt_version:
            return "old_prompt"
        if any(self.announced_version[s] > v for s, v in zip(truth.answer.sources, truth.versions)):
            return "ignored_invalidation"  # the client was told this document changed
        return None

    # ------------------------------------------------------------------ metrics
    def metrics(self) -> dict[str, Any]:
        finished = [t for t in self.turns if t.end is not None]
        good = [t for t in finished if t.good]
        lat = sorted(t.end - t.start for t in good if t.end is not None)

        def pct(q: float) -> float | None:
            if not lat:
                return None
            return round(lat[min(len(lat) - 1, int(q * len(lat)))], 3)

        attempts = sum(self.attempts_by_provider.values())
        m: dict[str, Any] = {
            "scenario": self.sc.key,
            "client": self.client.name,
            "turns": len(self.turns),
            "good_turns": len(good),
            "availability": round(len(good) / max(1, len(self.turns)), 4),
            "latency_p50_s": pct(0.50),
            "latency_p95_s": pct(0.95),
            "latency_p99_s": pct(0.99),
            "calls": self.calls_total,
            "attempts": attempts,
            "amplification": round(attempts / max(1, self.calls_total), 3),
            "retry_ratio": round(self.same_provider_retries / max(1, self.calls_total), 3),
            "attempts_by_provider": dict(self.attempts_by_provider),
            "errors_by_code": dict(self.errors_by_code),
            "give_up_reasons": dict(self.give_up_reasons),
            "cost": round(self.cost, 2),
            "cost_per_good_turn": round(self.cost / max(1, len(good)), 3),
            "retry_after_violations": self.retry_after_violations,
            "client_error_retries": self.client_error_retries,
            "incompatible_routes": self.incompatible_routes,
            "unneeded_failovers": self.unneeded_failovers,
        }
        m.update(self._recovery_metrics())
        if self.sc.faq is not None:
            faq = [t for t in self.turns if t.request is not None]
            m.update({
                "faq_turns": len(faq),
                "faq_availability": round(sum(t.good for t in faq) / max(1, len(faq)), 4),
                "cache_hit_ratio": round(self.cache_served / max(1, len(faq)), 3),
                "stale_served": self.stale_served,
                "wrong_answers": sum(self.wrong_answers.values()),
                "wrong_answers_by_reason": dict(self.wrong_answers),
                "outdated_answers": self.outdated_answers,
            })
        return m

    def _recovery_metrics(self) -> dict[str, Any]:
        if self.sc.restore_at is None:
            return {"recovery_s": None, "primary_share_after_restore": None}
        restore = float(self.sc.restore_at)
        end = int(self.sc.duration_s)
        recovery: float | None = None
        for s in range(int(restore), end - 30 + 1):
            ok = sum(self.per_second_calls[x][0] for x in range(s, s + 30))
            tot = sum(self.per_second_calls[x][1] for x in range(s, s + 30))
            if tot > 0 and ok / tot >= 0.95:
                recovery = float(s - restore)
                break
        primary = total = 0
        for s in range(int(restore) + 60, end):
            c = self.per_second_attempts[s]
            primary += c[self.primary]
            total += sum(c.values())
        share = round(primary / total, 3) if total else None
        return {"recovery_s": recovery, "primary_share_after_restore": share}

    def timeseries(self) -> list[dict[str, Any]]:
        rows = []
        names = [p.name for p in self.cfg.providers]
        for s in range(int(self.sc.duration_s + TAIL_S)):
            ok, tot = self.per_second_calls[s]
            row: dict[str, Any] = {"t": s, "calls_ok": ok, "calls_done": tot}
            for n in names:
                row[f"attempts_{n}"] = self.per_second_attempts[s][n]
            if self.sc.faq is not None:
                row["cache_served"] = self.per_second_cache[s]
            rows.append(row)
        return rows


def run_scenario(cfg: LabConfig, scenario_key: str, client: BaseClient, seed: int | None = None) -> Simulation:
    return Simulation(cfg, cfg.scenarios[scenario_key], client, seed).run()


def evaluate_checks(metrics: dict[str, Any], checks: dict[str, Any]) -> dict[str, bool]:
    """``checks`` keys look like ``availability_min`` / ``amplification_max``."""
    results: dict[str, bool] = {}
    for key, bound in checks.items():
        name, _, kind = key.rpartition("_")
        value = metrics.get(name)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            results[key] = False
        elif kind == "min":
            results[key] = value >= bound
        elif kind == "max":
            results[key] = value <= bound
        else:
            raise ValueError(f"bad check {key!r}")
    return results
