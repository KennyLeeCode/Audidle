"""Tests for the MusicBrainz provider.

No live calls. The transport is stubbed, so these run offline and in CI.

Two things get the most attention, because both are things that only show up
against the real service and are painful to debug there: the rate limiter, and
throttling behaviour. MusicBrainz allows about one request per second and sends
503 when exceeded, so a client that bursts or treats 503 as fatal will appear to
work in testing and fail during a real ingestion run.
"""

import asyncio
import time

import httpx
import pytest

from app.core.cache import TtlCache
from app.core.errors import CatalogRateLimitedError, CatalogUnavailableError
from app.core.ratelimit import RateLimiter
from app.providers.musicbrainz.musicbrainz_provider import (
    MusicBrainzProvider,
    _parse_recording,
)

RECORDING = {
    "id": "b1a9c0e9-d987-4042-ae91-78d6a3267d69",
    "title": "Blinding Lights",
    "length": 200040,
    "first-release-date": "2019-11-29",
    "isrcs": ["USUG11904206", "USUG12000001"],
    "artist-credit": [
        {
            "name": "The Weeknd",
            "joinphrase": "",
            "artist": {
                "id": "c8b03190-306c-4120-bb0b-6f2ebfc06ea9",
                "name": "The Weeknd",
                "sort-name": "Weeknd, The",
            },
        }
    ],
    "tags": [
        {"name": "synthpop", "count": 5},
        {"name": "pop", "count": 2},
        # A tag the community voted down. Should be dropped.
        {"name": "nonsense", "count": 0},
    ],
    "score": 100,
}


class StubTransport(httpx.AsyncBaseTransport):
    """Serves canned responses and records what was asked for."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(200, json={})


def make_provider(responses: list[httpx.Response], rate: float = 1000.0):
    """A provider with a stub transport and effectively no rate limiting."""
    provider = MusicBrainzProvider(
        user_agent="Audidle/test", contact="test@example.com", requests_per_second=rate
    )
    transport = StubTransport(responses)
    provider._http = httpx.AsyncClient(transport=transport, headers=provider._headers)
    return provider, transport


# -- Policy compliance -------------------------------------------------------


def test_contact_information_is_required():
    """Their policy mandates it, and requests without it get blocked."""
    with pytest.raises(ValueError, match="contact"):
        MusicBrainzProvider(user_agent="Audidle/0.1", contact="")


@pytest.mark.anyio
async def test_requests_carry_a_descriptive_user_agent():
    provider, transport = make_provider([httpx.Response(200, json={"recordings": []})])

    await provider.search_recordings("anything")

    agent = transport.requests[0].headers["user-agent"]
    assert "Audidle" in agent
    assert "test@example.com" in agent


@pytest.mark.anyio
async def test_json_format_is_always_requested():
    provider, transport = make_provider([httpx.Response(200, json={"recordings": []})])

    await provider.search_recordings("anything")

    assert "fmt=json" in str(transport.requests[0].url)


# -- Rate limiting -----------------------------------------------------------


@pytest.mark.anyio
async def test_rate_limiter_spaces_calls_out():
    limiter = RateLimiter(requests_per_second=20)

    start = time.monotonic()
    for _ in range(4):
        await limiter.acquire()
    elapsed = time.monotonic() - start

    # Three gaps of 50ms between four calls.
    assert elapsed >= 0.14


@pytest.mark.anyio
async def test_rate_limiter_holds_under_concurrency():
    """Without the lock, concurrent callers would all fire at once."""
    limiter = RateLimiter(requests_per_second=20)

    start = time.monotonic()
    await asyncio.gather(*(limiter.acquire() for _ in range(5)))
    elapsed = time.monotonic() - start

    assert elapsed >= 0.19


def test_rate_limiter_rejects_a_nonsense_rate():
    with pytest.raises(ValueError):
        RateLimiter(requests_per_second=0)


# -- Throttling and failure --------------------------------------------------


@pytest.mark.anyio
async def test_a_503_is_retried_rather_than_failing():
    """MusicBrainz signals throttling with 503. It is expected, not fatal."""
    provider, transport = make_provider(
        [
            httpx.Response(503, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"recordings": [RECORDING]}),
        ]
    )

    found = await provider.search_recordings("Blinding Lights")

    assert len(found) == 1
    assert len(transport.requests) == 2


@pytest.mark.anyio
async def test_sustained_throttling_eventually_raises():
    provider, _ = make_provider(
        [httpx.Response(503, headers={"Retry-After": "0"}) for _ in range(3)]
    )

    with pytest.raises(CatalogRateLimitedError):
        await provider.search_recordings("anything")


@pytest.mark.anyio
async def test_server_errors_are_retried_then_raise():
    provider, transport = make_provider([httpx.Response(500) for _ in range(3)])

    with pytest.raises(CatalogUnavailableError):
        await provider.get_recording("some-mbid")

    assert len(transport.requests) == 3


@pytest.mark.anyio
async def test_a_404_means_not_found_rather_than_an_error():
    provider, _ = make_provider([httpx.Response(404)])

    assert await provider.get_recording("does-not-exist") is None


# -- Caching -----------------------------------------------------------------


@pytest.mark.anyio
async def test_repeat_recording_lookups_hit_the_cache():
    """At one request per second, every avoided call is a second saved."""
    provider, transport = make_provider([httpx.Response(200, json=RECORDING)])

    first = await provider.get_recording(RECORDING["id"])
    second = await provider.get_recording(RECORDING["id"])

    assert first == second
    assert len(transport.requests) == 1


@pytest.mark.anyio
async def test_search_results_prime_the_recording_cache():
    provider, transport = make_provider([httpx.Response(200, json={"recordings": [RECORDING]})])

    await provider.search_recordings("Blinding Lights", "The Weeknd")
    await provider.get_recording(RECORDING["id"])

    assert len(transport.requests) == 1


def test_ttl_cache_expires_entries():
    cache: TtlCache[str] = TtlCache(max_entries=4, ttl_seconds=-1)
    cache.set("k", "v")

    assert cache.get("k") is None


def test_ttl_cache_is_bounded():
    cache: TtlCache[int] = TtlCache(max_entries=2, ttl_seconds=600)
    for index in range(5):
        cache.set(str(index), index)

    assert len(cache) == 2


# -- Parsing -----------------------------------------------------------------


def test_parses_a_recording():
    recording = _parse_recording(RECORDING)

    assert recording.mbid == RECORDING["id"]
    assert recording.title == "Blinding Lights"
    assert recording.artist_credit == "The Weeknd"
    assert recording.primary_artist == "The Weeknd"
    assert recording.length_ms == 200040
    assert recording.first_release_year == 2019


def test_a_recording_can_carry_several_isrcs():
    """The reason identifiers are a table rather than a column."""
    recording = _parse_recording(RECORDING)

    assert recording.isrcs == ("USUG11904206", "USUG12000001")


def test_downvoted_tags_are_dropped():
    recording = _parse_recording(RECORDING)

    assert "nonsense" not in recording.tags
    assert set(recording.tags) == {"synthpop", "pop"}


def test_collaborations_keep_their_join_phrases():
    payload = {
        "id": "x",
        "title": "One Kiss",
        "artist-credit": [
            {"name": "Calvin Harris", "joinphrase": " & ", "artist": {"id": "a", "name": "Calvin Harris"}},
            {"name": "Dua Lipa", "joinphrase": "", "artist": {"id": "b", "name": "Dua Lipa"}},
        ],
    }

    recording = _parse_recording(payload)

    assert recording.artist_credit == "Calvin Harris & Dua Lipa"
    assert len(recording.artists) == 2
    assert recording.primary_artist == "Calvin Harris"


def test_sparse_records_do_not_break_parsing():
    """Community edited data is uneven and must not stop an ingestion run."""
    recording = _parse_recording({"id": "bare"})

    assert recording.mbid == "bare"
    assert recording.isrcs == ()
    assert recording.length_ms is None
    assert recording.first_release_year is None


def test_an_unparsable_date_is_ignored():
    recording = _parse_recording({"id": "x", "first-release-date": "unknown"})

    assert recording.first_release_year is None


@pytest.mark.anyio
async def test_query_terms_are_escaped():
    """Punctuation in a title must not be read as a query operator."""
    provider, transport = make_provider([httpx.Response(200, json={"recordings": []})])

    await provider.search_recordings('Wait (Reprise) [Remix]: "Live"', "AC/DC")

    url = str(transport.requests[0].url)
    for char in ["(", ")", "[", "]", "/"]:
        assert char not in url.split("query=")[1].split("&")[0].replace("%", "")
