"""Caching decorator for catalog providers.

Wraps any SongCatalogProvider rather than being built into the Spotify one, so
the caching policy is one concern in one place and applies to whatever provider
is active. This is the payoff of having an interface: behaviour can be layered
on without editing an implementation.

Two reasons it exists:

  - Autocomplete fires a request per keystroke burst. Repeat queries are very
    common, and Spotify's rate limits are shared across the whole app.
  - Track lookups repeat constantly, since every guess resolves a track and the
    reveal resolves the answer again.
"""

import time
from collections import OrderedDict

from app.models.song import Song
from app.providers.base import SongCatalogProvider


class _TtlCache:
    """Small LRU cache with a time to live.

    Bounded on both size and age. Metadata does change (a track can be
    relicensed or renamed), so entries expire rather than living forever.
    """

    def __init__(self, max_entries: int, ttl_seconds: float) -> None:
        self._entries: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl_seconds

    def get(self, key: str) -> object | None:
        entry = self._entries.get(key)
        if entry is None:
            return None

        stored_at, value = entry
        if time.monotonic() - stored_at > self._ttl:
            del self._entries[key]
            return None

        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: object) -> None:
        self._entries[key] = (time.monotonic(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class CachedSongCatalogProvider(SongCatalogProvider):
    """Adds caching to any catalog provider."""

    def __init__(
        self,
        inner: SongCatalogProvider,
        search_ttl_seconds: float = 300.0,
        song_ttl_seconds: float = 3600.0,
        max_entries: int = 512,
    ) -> None:
        self._inner = inner
        # Searches expire faster than tracks, since the catalog gains new
        # releases far more often than an existing track changes.
        self._searches = _TtlCache(max_entries, search_ttl_seconds)
        self._songs = _TtlCache(max_entries, song_ttl_seconds)

    @property
    def provider_name(self) -> str:
        return self._inner.provider_name

    async def resolve_external(self, provider: str, external_id: str) -> Song | None:
        return await self._inner.resolve_external(provider, external_id)

    async def search(self, query: str, limit: int = 10) -> list[Song]:
        # Normalized, so "Blinding" and " blinding " share one entry.
        key = f"{query.strip().lower()}:{limit}"
        cached = self._searches.get(key)
        if cached is not None:
            return list(cached)  # type: ignore[arg-type]

        results = await self._inner.search(query, limit=limit)
        self._searches.set(key, results)
        # Individual tracks are cached too, so guessing a song that appeared in
        # search needs no second request to resolve it.
        for song in results:
            self._songs.set(song.id, song)
        return results

    async def get_song(self, song_id: str) -> Song | None:
        cached = self._songs.get(song_id)
        if cached is not None:
            return cached  # type: ignore[return-value]

        song = await self._inner.get_song(song_id)
        if song is not None:
            self._songs.set(song_id, song)
        return song

    async def get_songs(self, song_ids: list[str]) -> list[Song]:
        found: list[Song] = []
        missing: list[str] = []

        for song_id in song_ids:
            cached = self._songs.get(song_id)
            if cached is not None:
                found.append(cached)  # type: ignore[arg-type]
            else:
                missing.append(song_id)

        # Only the cache misses reach the upstream provider, which matters most
        # for song selection, where the same tier is queried on every round.
        if missing:
            fetched = await self._inner.get_songs(missing)
            for song in fetched:
                self._songs.set(song.id, song)
            found.extend(fetched)

        return found
