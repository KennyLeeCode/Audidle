"""A small TTL cache, shared by the provider layer.

Bounded on both size and age. Metadata does change, so entries expire rather
than living forever, and the bound keeps a long ingestion run from growing
without limit.
"""

import time
from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


class TtlCache(Generic[T]):
    """Least recently used cache with a time to live."""

    def __init__(self, max_entries: int = 512, ttl_seconds: float = 3600.0) -> None:
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl_seconds

    def get(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None

        stored_at, value = entry
        if time.monotonic() - stored_at > self._ttl:
            del self._entries[key]
            return None

        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: T) -> None:
        self._entries[key] = (time.monotonic(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
