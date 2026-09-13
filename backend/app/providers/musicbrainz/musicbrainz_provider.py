"""MusicBrainz Web Service client.

The only module that talks to MusicBrainz. It is our recording identity layer:
it answers "which recording is this, precisely, and what are its ISRCs", which
is the question Spotify cannot answer well and the one that lets us match
across providers.

What it is deliberately not used for: discovering which songs are worth adding,
and popularity. MusicBrainz has no notion of either. The seed catalog decides
what we ingest and PopularityProvider decides difficulty.

Two requirements from their policy shape this file:

  - A descriptive User-Agent with contact information is mandatory. Requests
    without one get blocked.
  - Roughly one request per second for anonymous clients. Being throttled is
    the normal operating condition, not an edge case, so the limiter is built
    in rather than bolted on.

Caching matters for the same reason. A resumable ingestion re-reads the same
recordings constantly, and at one request per second every avoided call is a
second saved.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.cache import TtlCache
from app.core.errors import CatalogRateLimitedError, CatalogUnavailableError
from app.core.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

API_BASE = "https://musicbrainz.org/ws/2"

# Their documented anonymous limit. Deliberately not configurable upward.
DEFAULT_RATE_LIMIT = 1.0
MAX_RETRIES = 3


@dataclass(frozen=True)
class MusicBrainzArtist:
    mbid: str
    name: str
    sort_name: str | None = None


@dataclass(frozen=True)
class MusicBrainzRecording:
    """A recording as MusicBrainz describes it.

    Note `isrcs` is plural. A recording legitimately carries several, which is
    the reason external identifiers are a table rather than a column.
    """

    mbid: str
    title: str
    artist_credit: str
    artists: tuple[MusicBrainzArtist, ...] = field(default_factory=tuple)
    isrcs: tuple[str, ...] = field(default_factory=tuple)
    length_ms: int | None = None
    first_release_year: int | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    # Search relevance, 0 to 100. Only present on search results, and used as a
    # hint rather than as a match decision.
    score: int | None = None

    @property
    def primary_artist(self) -> str:
        return self.artists[0].name if self.artists else self.artist_credit


class MusicBrainzProvider:
    """Rate limited, cached access to the MusicBrainz Web Service."""

    def __init__(
        self,
        user_agent: str,
        contact: str,
        requests_per_second: float = DEFAULT_RATE_LIMIT,
        timeout: float = 15.0,
        cache_ttl_seconds: float = 86_400.0,
    ) -> None:
        if not contact:
            # Their policy requires it, and requests without it get blocked.
            # Failing here beats being silently throttled to nothing later.
            raise ValueError(
                "MusicBrainz requires contact information in the User-Agent. "
                "Set MUSICBRAINZ_CONTACT in .env"
            )

        self._headers = {
            "User-Agent": f"{user_agent} ( {contact} )",
            "Accept": "application/json",
        }
        self._http = httpx.AsyncClient(timeout=timeout, headers=self._headers)
        self._limiter = RateLimiter(requests_per_second)
        # Recordings are cached for a day. MusicBrainz data does change, but far
        # more slowly than an ingestion run re-reads it.
        self._recordings: TtlCache[MusicBrainzRecording] = TtlCache(
            max_entries=4096, ttl_seconds=cache_ttl_seconds
        )
        self._searches: TtlCache[list[MusicBrainzRecording]] = TtlCache(
            max_entries=1024, ttl_seconds=cache_ttl_seconds
        )
        self.request_count = 0

    # -- Transport ------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Perform a rate limited request with retries."""
        params = {**params, "fmt": "json"}

        for attempt in range(MAX_RETRIES):
            await self._limiter.acquire()
            self.request_count += 1

            try:
                response = await self._http.get(f"{API_BASE}/{path.lstrip('/')}", params=params)
            except httpx.HTTPError as error:
                if attempt == MAX_RETRIES - 1:
                    raise CatalogUnavailableError("Could not reach MusicBrainz") from error
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code == 404:
                # A genuine "not here". Callers turn this into None.
                return {}

            if response.status_code == 503:
                # How MusicBrainz signals throttling. Backing off further rather
                # than failing, since this is expected under sustained load.
                if attempt == MAX_RETRIES - 1:
                    raise CatalogRateLimitedError("MusicBrainz is throttling requests")
                wait = float(response.headers.get("Retry-After", 2**attempt))
                logger.warning("MusicBrainz throttled us, waiting %.1fs", wait)
                await asyncio.sleep(min(wait, 10.0))
                continue

            if response.status_code >= 500:
                if attempt == MAX_RETRIES - 1:
                    raise CatalogUnavailableError(
                        f"MusicBrainz returned {response.status_code}"
                    )
                await asyncio.sleep(2**attempt)
                continue

            raise CatalogUnavailableError(
                f"MusicBrainz request to '{path}' failed with {response.status_code}"
            )

        raise CatalogUnavailableError("MusicBrainz did not respond after several attempts")

    # -- Lookups --------------------------------------------------------------

    async def search_recordings(
        self, title: str, artist: str | None = None, limit: int = 10
    ) -> list[MusicBrainzRecording]:
        """Search for recordings by title, optionally narrowed by artist.

        Uses their Lucene query syntax, with the terms quoted so punctuation in
        a title cannot be read as query operators.
        """
        query = f'recording:"{_escape(title)}"'
        if artist:
            query += f' AND artist:"{_escape(artist)}"'

        cache_key = f"{query}:{limit}"
        cached = self._searches.get(cache_key)
        if cached is not None:
            return cached

        payload = await self._get("recording", {"query": query, "limit": limit})
        recordings = [
            _parse_recording(item) for item in payload.get("recordings", []) if item.get("id")
        ]

        self._searches.set(cache_key, recordings)
        for recording in recordings:
            self._recordings.set(recording.mbid, recording)
        return recordings

    async def get_recording(self, mbid: str) -> MusicBrainzRecording | None:
        """Fetch one recording with its artists, ISRCs, and tags."""
        cached = self._recordings.get(mbid)
        if cached is not None:
            return cached

        payload = await self._get(
            f"recording/{mbid}", {"inc": "artists+isrcs+releases+tags"}
        )
        if not payload:
            return None

        recording = _parse_recording(payload)
        self._recordings.set(mbid, recording)
        return recording

    async def get_recording_by_isrc(self, isrc: str) -> MusicBrainzRecording | None:
        """Find the recording carrying an ISRC.

        The strongest cross-provider link available. An ISRC identifies the
        recording rather than the release, so this is how a Spotify track and a
        MusicBrainz recording are known to be the same thing.

        Several recordings can share an ISRC in practice, usually because of
        duplicate MusicBrainz entries. The first is returned, and the caller
        decides whether the ambiguity matters.
        """
        payload = await self._get(f"isrc/{isrc.strip().upper()}", {"inc": "artists+isrcs"})
        recordings = payload.get("recordings", [])
        if not recordings:
            return None

        recording = _parse_recording(recordings[0])
        self._recordings.set(recording.mbid, recording)
        return recording

    async def get_recording_isrcs(self, mbid: str) -> tuple[str, ...]:
        """Every ISRC attached to a recording."""
        recording = await self.get_recording(mbid)
        return recording.isrcs if recording else ()

    async def get_artist(self, mbid: str) -> MusicBrainzArtist | None:
        payload = await self._get(f"artist/{mbid}", {})
        if not payload:
            return None
        return MusicBrainzArtist(
            mbid=payload["id"],
            name=payload.get("name", ""),
            sort_name=payload.get("sort-name"),
        )

    async def aclose(self) -> None:
        await self._http.aclose()


# -- Parsing ----------------------------------------------------------------


def _escape(value: str) -> str:
    """Escape Lucene special characters in a query term."""
    for char in ['"', "\\", "/", "?", "*", "~", "^", ":", "(", ")", "[", "]", "{", "}"]:
        value = value.replace(char, " ")
    return value.strip()


def _parse_recording(payload: dict[str, Any]) -> MusicBrainzRecording:
    """Map a MusicBrainz recording object onto our dataclass.

    Defensive throughout. Community edited data is uneven, and a recording with
    no artist credit or an unparsable date should not stop an ingestion run.
    """
    artists = tuple(
        MusicBrainzArtist(
            mbid=credit["artist"]["id"],
            name=credit["artist"].get("name", ""),
            sort_name=credit["artist"].get("sort-name"),
        )
        for credit in payload.get("artist-credit", [])
        if isinstance(credit, dict) and credit.get("artist", {}).get("id")
    )

    credit_string = "".join(
        f"{credit.get('name', '')}{credit.get('joinphrase', '')}"
        for credit in payload.get("artist-credit", [])
        if isinstance(credit, dict)
    ).strip()

    length = payload.get("length")

    return MusicBrainzRecording(
        mbid=payload["id"],
        title=payload.get("title", ""),
        artist_credit=credit_string or (artists[0].name if artists else ""),
        artists=artists,
        isrcs=tuple(payload.get("isrcs", []) or ()),
        length_ms=int(length) if isinstance(length, int | float) else None,
        first_release_year=_parse_year(payload.get("first-release-date")),
        tags=tuple(
            tag["name"]
            for tag in payload.get("tags", []) or ()
            # Negative counts mean the community voted the tag down.
            if tag.get("name") and tag.get("count", 0) > 0
        ),
        score=payload.get("score"),
    )


def _parse_year(value: str | None) -> int | None:
    """Read the year from a date of varying precision."""
    if not value:
        return None
    try:
        return int(str(value)[:4])
    except ValueError:
        return None
