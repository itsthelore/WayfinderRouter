"""Tests for the deterministic exact-match response cache (WF-ADR-0033)."""

from __future__ import annotations

from wayfinder_router import cache


class _Clock:
    """A controllable monotonic clock for TTL tests (mirrors test_reliability's style)."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _entry(body: bytes = b"hello", pt: int = 3, ct: int = 5, at: float = 0.0) -> cache.CachedResponse:
    return cache.CachedResponse(
        status=200, content_type="application/json", body=body,
        prompt_tokens=pt, completion_tokens=ct, estimated=False, stored_at=at,
    )


# --- cache_key: pure, deterministic, served-model-aware ---------------------------------
def test_cache_key_is_order_and_stream_independent():
    a = cache.cache_key("m-cloud", {"model": "auto", "stream": False,
                                    "messages": [{"role": "user", "content": "hi"}], "temperature": 0})
    # same request, dict keys in a different order, stream toggled, routing directive changed
    b = cache.cache_key("m-cloud", {"temperature": 0, "stream": True, "model": "prefer-hosted",
                                    "messages": [{"content": "hi", "role": "user"}]})
    assert a == b and len(a) == 64


def test_cache_key_sensitive_to_inputs():
    base = {"messages": [{"role": "user", "content": "hi"}], "temperature": 0}
    k = cache.cache_key("m-cloud", base)
    assert k != cache.cache_key("m-cloud", {**base, "temperature": 0.7})  # sampling differs
    assert k != cache.cache_key("m-cloud", {"messages": [{"role": "user", "content": "yo"}]})
    assert k != cache.cache_key("m-local", base)  # different served model never collides


# --- is_cacheable: the determinism gate -------------------------------------------------
def test_is_cacheable_accepts_deterministic_request():
    assert cache.is_cacheable({"messages": [{"role": "user", "content": "hi"}]})
    assert cache.is_cacheable({"messages": [{"role": "user", "content": "hi"}], "temperature": 0,
                               "top_p": 1, "n": 1, "max_tokens": 50})


def test_is_cacheable_rejects_nondeterministic_and_risky():
    msgs = [{"role": "user", "content": "hi"}]
    assert not cache.is_cacheable({"messages": msgs, "stream": True})
    assert not cache.is_cacheable({"messages": msgs, "temperature": 0.7})
    assert not cache.is_cacheable({"messages": msgs, "top_p": 0.9})
    assert not cache.is_cacheable({"messages": msgs, "n": 2})
    assert not cache.is_cacheable({"messages": msgs, "seed": 42})
    assert not cache.is_cacheable({"messages": msgs, "tools": [{"type": "function"}]})
    assert not cache.is_cacheable({"messages": msgs, "tool_choice": "auto"})
    assert not cache.is_cacheable({"messages": msgs, "logit_bias": {"50256": -100}})
    assert not cache.is_cacheable({"messages": []})  # empty/malformed
    assert not cache.is_cacheable({})  # no messages
    # multimodal / array content is not an exact-match candidate
    assert not cache.is_cacheable({"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]})


# --- is_storable: never cache an HTTP-200 error/empty body ------------------------------
def test_is_storable_accepts_a_real_completion():
    resp = {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
    assert cache.is_storable(200, "application/json", resp)


def test_is_storable_rejects_errors_empties_and_tools():
    good = {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
    assert not cache.is_storable(500, "application/json", good)  # not 200
    assert not cache.is_storable(200, "text/plain", good)  # not json
    assert not cache.is_storable(200, "application/json", {"error": {"message": "overloaded"}})
    assert not cache.is_storable(200, "application/json", {"choices": []})  # empty choices
    assert not cache.is_storable(200, "application/json",
                                 {"choices": [{"message": {"content": ""}}]})  # empty content
    assert not cache.is_storable(200, "application/json",
                                 {"choices": [{"message": {"content": None}}]})  # non-str content
    assert not cache.is_storable(200, "application/json",
                                 {"choices": [{"message": {"content": "x", "tool_calls": [{}]}}]})


# --- ResponseCache: LRU + byte ceiling + TTL + enable/reconfigure -----------------------
def test_get_put_roundtrip_and_disabled_is_noop():
    c = cache.ResponseCache(enabled=False, ttl=0)  # ttl=0 -> no expiry (TTL tested separately)
    c.put("k", _entry())
    assert c.get("k") is None  # disabled never stores or serves
    assert c.stats()["entries"] == 0
    c.enabled = True
    c.put("k", _entry())
    assert c.get("k") is not None


def test_lru_eviction_by_entry_count():
    c = cache.ResponseCache(enabled=True, max_entries=2, max_bytes=10**9, ttl=0)
    c.put("a", _entry())
    c.put("b", _entry())
    assert c.get("a") is not None  # touch a -> most recently used
    c.put("c", _entry())  # over the count -> evict the LRU, which is now b
    assert c.get("b") is None
    assert c.get("a") is not None and c.get("c") is not None


def test_byte_ceiling_eviction():
    c = cache.ResponseCache(enabled=True, max_entries=10**6, max_bytes=150, ttl=0)
    c.put("a", _entry(body=b"x" * 100))
    c.put("b", _entry(body=b"y" * 100))  # 200 > 150 -> evict a
    assert c.get("a") is None and c.get("b") is not None
    c.put("big", _entry(body=b"z" * 200))  # single entry larger than the ceiling -> never stored
    assert c.get("big") is None


def test_ttl_expiry_with_injected_clock():
    clock = _Clock()
    c = cache.ResponseCache(enabled=True, ttl=10.0, clock=clock)
    c.put("k", _entry(at=clock()))
    assert c.get("k") is not None
    clock.advance(10.0)  # reaches ttl -> expired
    assert c.get("k") is None
    assert c.stats()["entries"] == 0  # expired entry was dropped


def test_reconfigure_disable_purges_and_shrink_evicts():
    c = cache.ResponseCache(enabled=True, max_entries=3, max_bytes=10**9)
    for k in ("a", "b", "c"):
        c.put(k, _entry())
    c.reconfigure(enabled=True, max_entries=1, max_bytes=10**9, ttl=300.0)
    assert c.stats()["entries"] == 1  # shrunk -> evicted down to the cap
    c.reconfigure(enabled=False, max_entries=1, max_bytes=10**9, ttl=300.0)
    assert c.stats()["entries"] == 0 and c.stats()["bytes"] == 0  # disable purges bodies


def test_hit_miss_counters():
    c = cache.ResponseCache(enabled=True, ttl=0)
    c.put("k", _entry())
    c.get("k")
    c.get("missing")
    s = c.stats()
    assert s["hits"] == 1 and s["misses"] == 1
