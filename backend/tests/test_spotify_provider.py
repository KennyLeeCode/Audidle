"""Tests for the Spotify catalog provider and the caching wrapper.

No live network calls. The client is stubbed, so these run offline, in CI, and
without credentials. What they actually verify is the mapping layer, which is
where real catalog data causes trouble: missing artwork, missing albums, partial
release dates, and collaborations.

The response fixtures below match the real shape this app's credentials return,
captured from the live API. Notably they contain no `preview_url` and no
`popularity` key at all, because Spotify does not send them to apps created
after November 2024.
"""

import pytest

from app.models.song import Song
from app.providers.base import SongCatalogProvider
from app.providers.caching import CachedSongCatalogProvider
from app.providers.spotify.spotify_song_provider import (
    MAX_SEARCH_LIMIT,
    SpotifySongProvider,
)

# A real track object, trimmed. Note what is absent.
TRACK = {
    "id": "0VjIjW4GlUZAMYd2vXMi3b",
    "name": "Blinding Lights",
    "duration_ms": 200040,
    "explicit": False,
    "external_urls": {"spotify": "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b"},
    "artists": [{"name": "The Weeknd", "id": "1Xyo4u8uXC1ZmMpatF05PJ"}],
    "album": {
        "name": "After Hours",
        "release_date": "2020-03-20",
        "images": [
            {"height": 640, "width": 640, "url": "https://i.scdn.co/image/large"},
            {"height": 300, "width": 300, "url": "https://i.scdn.co/image/medium"},
            {"height": 64, "width": 64, "url": "https://i.scdn.co/image/small"},
        ],
    },
}


class StubClient:
    """Records calls and returns canned payloads."""

    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append((path, params))
        return self.payloads.get(path, {})


def make_provider(payloads: dict[str, dict], market: str | None = "US"):
    client = StubClient(payloads)
    return SpotifySongProvider(client=client, market=market), client  # type: ignore[arg-type]


# -- Mapping ----------------------------------------------------------------


@pytest.mark.anyio
async def test_maps_a_track_onto_the_domain_model():
    provider, _ = make_provider({"tracks/0VjIjW4GlUZAMYd2vXMi3b": TRACK})

    song = await provider.get_song("0VjIjW4GlUZAMYd2vXMi3b")

    assert song is not None
    assert song.id == "0VjIjW4GlUZAMYd2vXMi3b"
    assert song.title == "Blinding Lights"
    assert song.artist == "The Weeknd"
    assert song.album == "After Hours"
    assert song.release_year == 2020
    assert song.duration_ms == 200040
    assert song.explicit is False
    # Spotify exposes no track genres to this app, so this is empty rather
    # than guessed at.
    assert song.genres == ()


@pytest.mark.anyio
async def test_prefers_mid_sized_artwork():
    """The 300px cover suits both the search row and the reveal card."""
    provider, _ = make_provider({"tracks/x": {**TRACK, "id": "x"}})

    song = await provider.get_song("x")

    assert song is not None
    assert song.artwork_url == "https://i.scdn.co/image/medium"


@pytest.mark.anyio
async def test_joins_collaborating_artists():
    track = {**TRACK, "id": "c", "artists": [{"name": "Artist A"}, {"name": "Artist B"}]}
    provider, _ = make_provider({"tracks/c": track})

    song = await provider.get_song("c")

    assert song is not None
    assert song.artist == "Artist A, Artist B"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("release_date", "expected"),
    [("2020-03-20", 2020), ("1997-06", 1997), ("1984", 1984), (None, None), ("", None)],
)
async def test_parses_every_release_date_precision(release_date, expected):
    """Spotify varies between day, month, and year precision."""
    track = {**TRACK, "id": "d", "album": {**TRACK["album"], "release_date": release_date}}
    provider, _ = make_provider({"tracks/d": track})

    song = await provider.get_song("d")

    assert song is not None
    assert song.release_year == expected


@pytest.mark.anyio
async def test_missing_artwork_and_album_do_not_break_mapping():
    """Both are common in real catalog data and must not cost a round."""
    track = {"id": "bare", "name": "Bare Track", "artists": [{"name": "Someone"}]}
    provider, _ = make_provider({"tracks/bare": track})

    song = await provider.get_song("bare")

    assert song is not None
    assert song.artwork_url is None
    assert song.album is None
    assert song.duration_ms is None


@pytest.mark.anyio
async def test_unknown_track_returns_none():
    """The client turns a 404 into an empty dict, which maps to None."""
    provider, _ = make_provider({})

    assert await provider.get_song("missing") is None


@pytest.mark.anyio
async def test_malformed_items_are_skipped_not_raised():
    payload = {"tracks": {"items": [TRACK, {}, None, {"name": "no id"}]}}
    provider, _ = make_provider({"search": payload})

    results = await provider.search("blinding")

    assert len(results) == 1
    assert results[0].title == "Blinding Lights"


# -- Requests ---------------------------------------------------------------


@pytest.mark.anyio
async def test_search_passes_market_and_caps_the_limit():
    provider, client = make_provider({"search": {"tracks": {"items": []}}})

    await provider.search("test", limit=500)

    path, params = client.calls[0]
    assert path == "search"
    assert params["type"] == "track"
    assert params["market"] == "US"
    # Measured, not from the docs. The documented ceiling is 50, but an app
    # created after Spotify's November 2024 restrictions gets HTTP 400 for
    # anything above 10, even on queries whose result set is larger.
    assert params["limit"] == MAX_SEARCH_LIMIT
    assert MAX_SEARCH_LIMIT == 10


@pytest.mark.anyio
async def test_batch_lookup_is_chunked_to_the_api_limit():
    """Spotify accepts at most 50 ids per call."""
    provider, client = make_provider({"tracks": {"tracks": []}})

    await provider.get_songs([f"id{index}" for index in range(120)])

    assert len(client.calls) == 3
    assert len(client.calls[0][1]["ids"].split(",")) == 50
    assert len(client.calls[2][1]["ids"].split(",")) == 20


@pytest.mark.anyio
async def test_batch_lookup_drops_nulls_for_unknown_ids():
    provider, _ = make_provider({"tracks": {"tracks": [TRACK, None]}})

    songs = await provider.get_songs(["0VjIjW4GlUZAMYd2vXMi3b", "nope"])

    assert len(songs) == 1


# -- Caching ----------------------------------------------------------------


class CountingProvider(SongCatalogProvider):
    """Counts how often it is actually reached, to prove caching works."""

    @property
    def provider_name(self) -> str:
        return "counting"

    def __init__(self) -> None:
        self.search_calls = 0
        self.song_calls = 0
        self.batch_ids: list[list[str]] = []

    async def search(self, query: str, limit: int = 10) -> list[Song]:
        self.search_calls += 1
        return [Song(id="a", title="A Song", artist="An Artist")]

    async def get_song(self, song_id: str) -> Song | None:
        self.song_calls += 1
        return Song(id=song_id, title="A Song", artist="An Artist")

    async def get_songs(self, song_ids: list[str]) -> list[Song]:
        self.batch_ids.append(song_ids)
        return [Song(id=sid, title="A Song", artist="An Artist") for sid in song_ids]


@pytest.mark.anyio
async def test_repeat_searches_hit_the_cache():
    inner = CountingProvider()
    cached = CachedSongCatalogProvider(inner)

    await cached.search("blinding")
    await cached.search("blinding")
    # Normalization means whitespace and case share one entry.
    await cached.search("  BLINDING  ")

    assert inner.search_calls == 1


@pytest.mark.anyio
async def test_search_results_prime_the_song_cache():
    """Guessing a song that appeared in search needs no second request."""
    inner = CountingProvider()
    cached = CachedSongCatalogProvider(inner)

    await cached.search("anything")
    song = await cached.get_song("a")

    assert song is not None
    assert inner.song_calls == 0


@pytest.mark.anyio
async def test_batch_lookup_only_fetches_cache_misses():
    inner = CountingProvider()
    cached = CachedSongCatalogProvider(inner)

    await cached.get_songs(["one", "two"])
    results = await cached.get_songs(["one", "two", "three"])

    assert len(results) == 3
    # The second call fetched only the id it had never seen.
    assert inner.batch_ids[1] == ["three"]


@pytest.mark.anyio
async def test_expired_entries_are_refetched():
    inner = CountingProvider()
    cached = CachedSongCatalogProvider(inner, search_ttl_seconds=-1)

    await cached.search("q")
    await cached.search("q")

    assert inner.search_calls == 2
