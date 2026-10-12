"""Part G — SemanticCache and CachingClient, tested without the simulator.

The CachingClient tests use a stub instead of your ResilientClient, so Part G
can be done even if Part C is unfinished.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import pytest

from gameday_lab.cache import CacheHit, CachingClient, SemanticCache
from gameday_lab.sim.contracts import (
    Action,
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

Q = (1.0, 0.0, 0.0)
_ids = itertools.count(1)


def near(cos: float) -> tuple[float, float, float]:
    """A unit vector whose cosine with Q is ``cos``."""
    return (cos, math.sqrt(1.0 - cos * cos), 0.0)


def req(emb: tuple[float, ...] = Q, *, user: str = "u1", personal: bool = False, version: str = "v1") -> Request:
    return Request("Chính sách hoàn tiền thế nào?", user, personal, version, emb)


def answer(version: str = "v1", sources: tuple[str, ...] = ("policy/refund",)) -> Answer:
    return Answer(next(_ids), version, sources, 0.0)


def cache(**kw: Any) -> SemanticCache:
    settings: dict[str, Any] = {"similarity_threshold": 0.9, "ttl_s": 60.0, "max_stale_s": 300.0, "max_entries": 10}
    settings.update(kw)
    return SemanticCache(**settings)


# ---------------------------------------------------------------- SemanticCache
def test_same_question_is_a_fresh_hit() -> None:
    c = cache()
    a = answer()
    c.store(req(), a, 0.0)
    hit = c.lookup(req(), 10.0)
    assert isinstance(hit, CacheHit) and hit.answer == a and hit.fresh
    assert hit.similarity == pytest.approx(1.0) and hit.age_s == pytest.approx(10.0)


def test_paraphrase_above_threshold_hits_near_miss_below_does_not() -> None:
    c = cache(similarity_threshold=0.9)
    a = answer()
    c.store(req(Q), a, 0.0)
    hit = c.lookup(req(near(0.95)), 1.0)
    assert hit is not None and hit.answer == a
    assert c.lookup(req(near(0.85)), 1.0) is None  # "refund time" is not "refund policy"


def test_picks_the_most_similar_entry() -> None:
    c = cache(similarity_threshold=0.9)
    a1, a2 = answer(), answer()
    c.store(req(near(0.92)), a1, 0.0)
    c.store(req((0.97, 0.0, math.sqrt(1 - 0.97**2))), a2, 0.0)  # 0.89 from the first: a different entry
    assert len(c.entries) == 2
    hit = c.lookup(req(Q), 1.0)
    assert hit is not None and hit.answer == a2


def test_personal_questions_are_never_cached() -> None:
    c = cache()
    c.store(req(personal=True, user="u1"), answer(sources=("account/u1",)), 0.0)
    assert c.entries == []
    c.store(req(), answer(), 0.0)
    assert c.lookup(req(personal=True, user="u2"), 1.0) is None


def test_scope_by_prompt_version() -> None:
    c = cache()
    c.store(req(version="v1"), answer("v1"), 0.0)
    assert c.lookup(req(version="v2"), 1.0) is None
    assert c.lookup(req(version="v1"), 1.0) is not None


def test_ttl_then_stale_window_then_expired() -> None:
    c = cache(ttl_s=60.0, max_stale_s=300.0)
    a = answer()
    c.store(req(), a, 0.0)
    fresh = c.lookup(req(), 60.0)
    assert fresh is not None and fresh.fresh
    stale = c.lookup(req(), 200.0)
    assert stale is not None and not stale.fresh and stale.answer == a
    assert c.lookup(req(), 361.0) is None


def test_invalidate_by_source() -> None:
    c = cache()
    c.store(req(Q), answer(sources=("policy/refund",)), 0.0)
    c.store(req((0.0, 1.0, 0.0)), answer(sources=("policy/shipping",)), 0.0)
    assert c.invalidate("policy/refund", 5.0) == 1
    assert c.lookup(req(Q), 6.0) is None
    assert c.lookup(req((0.0, 1.0, 0.0)), 6.0) is not None


def test_store_refreshes_the_same_question() -> None:
    c = cache()
    a1, a2 = answer(), answer()
    c.store(req(Q), a1, 0.0)
    c.store(req(near(0.97)), a2, 100.0)
    assert len(c.entries) == 1
    hit = c.lookup(req(Q), 101.0)
    assert hit is not None and hit.answer == a2 and hit.fresh


def test_bounded_size_evicts_least_recently_used() -> None:
    c = cache(max_entries=2)
    x, y, z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    c.store(req(x), answer(), 0.0)
    c.store(req(y), answer(), 1.0)
    assert c.lookup(req(x), 2.0) is not None  # x is now the most recently used
    c.store(req(z), answer(), 3.0)
    assert len(c.entries) == 2
    assert c.lookup(req(y), 4.0) is None
    assert c.lookup(req(x), 4.0) is not None and c.lookup(req(z), 4.0) is not None


# ---------------------------------------------------------------- CachingClient
class StubInner(BaseClient):
    """Stands in for your ResilientClient: answers with ``next_action`` and records the events it sees."""

    name = "stub"

    def __init__(self) -> None:
        self.next_action: Action = Send("alpha", timeout_s=8.0)
        self.events: list[str] = []

    def start(self, call: CallContext, now: float) -> Action:
        self.events.append("start")
        return self.next_action

    def on_success(self, attempt: Attempt, now: float, latency_s: float) -> None:
        self.events.append("success")

    def on_error(self, attempt: Attempt, err: ProviderError, now: float) -> Action:
        self.events.append("error")
        return self.next_action


def make_client(**kw: Any) -> tuple[CachingClient, StubInner]:
    inner = StubInner()
    return CachingClient(inner, cache(**kw)), inner


def faq_call(r: Request | None = None, cid: int = 1, now: float = 0.0) -> CallContext:
    return CallContext(cid, cid, "chat", now, now + 30.0, request=r or req())


def answered(cc: CachingClient, r: Request | None = None, a: Answer | None = None, now: float = 0.0) -> Answer:
    """One FAQ call that goes to the provider and succeeds at ``now + 1``."""
    c = faq_call(r, cid=next(_ids), now=now)
    assert isinstance(cc.start(c, now), Send)
    c.answer = a or answer()
    cc.on_success(Attempt(c, "alpha", 1, now, None), now + 1.0, 1.0)
    return c.answer


def test_miss_goes_to_the_provider_and_the_answer_is_stored() -> None:
    cc, inner = make_client()
    a = answered(cc)
    assert inner.events == ["start", "success"]  # the inner client (breaker) saw the result
    action = cc.start(faq_call(cid=2, now=5.0), 5.0)
    assert isinstance(action, Serve) and action.answer == a and not action.stale
    assert inner.events == ["start", "success"]  # fresh hit: no provider call at all


def test_calls_without_a_request_bypass_the_cache() -> None:
    cc, inner = make_client()
    c = CallContext(1, 1, "tool", 0.0, 30.0)
    assert isinstance(cc.start(c, 0.0), Send)
    cc.on_success(Attempt(c, "alpha", 1, 0.0, None), 1.0, 1.0)
    assert cc.cache.entries == [] and inner.events == ["start", "success"]


def test_past_ttl_revalidates_while_providers_are_healthy() -> None:
    cc, _ = make_client(ttl_s=60.0, max_stale_s=600.0)
    answered(cc)
    assert isinstance(cc.start(faq_call(cid=2, now=100.0), 100.0), Send)


def test_stale_answer_when_the_providers_give_up_at_start() -> None:
    cc, inner = make_client(ttl_s=60.0, max_stale_s=600.0)
    a = answered(cc)
    inner.next_action = GiveUp("no_provider_available")
    action = cc.start(faq_call(cid=2, now=200.0), 200.0)
    assert isinstance(action, Serve) and action.answer == a and action.stale


def test_stale_answer_when_the_providers_give_up_after_an_error() -> None:
    cc, inner = make_client(ttl_s=60.0, max_stale_s=600.0)
    a = answered(cc)
    c = faq_call(cid=2, now=100.0)
    assert isinstance(cc.start(c, 100.0), Send)
    inner.next_action = GiveUp("no_provider_available")
    err = ProviderError("alpha", 503, "service_unavailable")
    action = cc.on_error(Attempt(c, "alpha", 1, 100.0, None), err, 100.1)
    assert isinstance(action, Serve) and action.answer == a and action.stale


def test_give_up_without_a_cached_answer_passes_through() -> None:
    cc, inner = make_client()
    inner.next_action = GiveUp("no_provider_available")
    assert isinstance(cc.start(faq_call(), 0.0), GiveUp)


def test_content_update_invalidates_even_for_stale_use() -> None:
    cc, inner = make_client()
    answered(cc, a=answer(sources=("policy/refund",)))
    cc.on_content_update("policy/refund", 2.0)
    inner.next_action = GiveUp("no_provider_available")
    assert isinstance(cc.start(faq_call(cid=2, now=3.0), 3.0), GiveUp)


def test_personal_answer_is_never_served_to_anyone() -> None:
    cc, inner = make_client()
    answered(cc, req(personal=True, user="u1"), answer(sources=("account/u1",)))
    inner.next_action = GiveUp("no_provider_available")
    assert isinstance(cc.start(faq_call(req(personal=True, user="u2"), cid=2, now=2.0), 2.0), GiveUp)
    assert isinstance(cc.start(faq_call(req(personal=True, user="u1"), cid=3, now=2.0), 2.0), GiveUp)
