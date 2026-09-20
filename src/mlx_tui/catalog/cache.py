"""Session search-result cache; revision facts are cached separately."""

from __future__ import annotations

import threading
import time

SEARCH_TTL_SECONDS = 24 * 60 * 60


class SearchCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, int], tuple[float, tuple[str, ...]]] = {}

    def get(self, key: tuple[str, str, int]) -> list[str] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            saved, ids = entry
            if time.monotonic() - saved >= SEARCH_TTL_SECONDS:
                del self._entries[key]
                return None
            return list(ids)

    def put(self, key: tuple[str, str, int], ids: list[str]) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), tuple(ids))

    def invalidate(self, key: tuple[str, str, int]) -> None:
        with self._lock:
            self._entries.pop(key, None)
