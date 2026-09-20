"""Search cache expiry and explicit refresh behavior."""

from __future__ import annotations

import mlx_tui.catalog.cache as cache_module
from mlx_tui.catalog.cache import SEARCH_TTL_SECONDS, SearchCache


def test_search_cache_expires_and_invalidation_is_scoped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    now = 100.0
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: now)
    cache = SearchCache()
    first = ("qwen", "", 50)
    second = ("qwen", "other", 50)
    cache.put(first, ["a/model"])
    cache.put(second, ["b/model"])
    assert cache.get(first) == ["a/model"]
    cache.invalidate(first)
    assert cache.get(first) is None
    assert cache.get(second) == ["b/model"]
    now += SEARCH_TTL_SECONDS
    assert cache.get(second) is None
