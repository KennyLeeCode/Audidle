"""ListenBrainz client, used as a secondary popularity signal.

Added to cover the one thing YouTube is systematically bad at: songs whose
audience is not on YouTube. The 89 song calibration showed this clearly, with
Redbone, One Dance, and several Frank Ocean tracks scoring far below their real
familiarity because no official video exists.

ListenBrainz is a good complement because it is keyed on MusicBrainz recording
ids, which the catalog already holds, so there is no matching step and no
matching risk. Lookups are exact by construction.

Two metrics are collected and kept apart:

    total_listen_count    how many plays were reported
    total_user_count      how many distinct people played it

Unique listeners matters more for Audidle. The game asks whether a person would
recognize a song, and a thousand plays by one devoted fan says much less about
that than a thousand different people each playing it once.

Neither number is a Spotify stream count and neither is comparable to a YouTube
view count. They are normalized independently.
"""

import logging
from dataclasses import dataclass

import httpx

from app.core.errors import CatalogRateLimitedError, CatalogUnavailableError

logger = logging.getLogger(__name__)

API_BASE = "https://api.listenbrainz.org/1"

# The bulk endpoint accepts many recording ids per call. Kept modest so a
# failure loses little work and the payload stays small.
MAX_MBIDS_PER_CALL = 50

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class RecordingPopularity:
    """What ListenBrainz knows about one recording.

    Both counts are optional. A recording that exists in MusicBrainz but that
    nobody on ListenBrainz has played returns zeros or nothing at all, and those
    are different from "we did not look", which is why the caller distinguishes
    a missing record from a zero.
    """

    recording_mbid: str
    listen_count: int | None = None
    listener_count: int | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.listen_count or self.listener_count)


class ListenBrainzProvider:
    """Bulk popularity lookups by MusicBrainz recording id.

    No authentication and no quota, which is most of why this is a good second
    signal: the entire catalog can be refreshed whenever we like.
    """

    def __init__(self, timeout: float = 20.0, user_agent: str = "Audidle/0.1") -> None:
        self._http = httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": user_agent}
        )
        self.request_count = 0

    async def _post(self, path: str, payload: dict) -> list[dict]:
        for attempt in range(MAX_RETRIES):
            self.request_count += 1
            try:
                response = await self._http.post(f"{API_BASE}/{path}", json=payload)
            except httpx.HTTPError as error:
                if attempt == MAX_RETRIES - 1:
                    raise CatalogUnavailableError("Could not reach ListenBrainz") from error
                await _sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                continue

            if response.status_code == 200:
                body = response.json()
                return body if isinstance(body, list) else []

            if response.status_code == 429:
                if attempt == MAX_RETRIES - 1:
                    raise CatalogRateLimitedError("ListenBrainz is rate limiting requests")
                await _sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                continue

            if response.status_code >= 500:
                if attempt == MAX_RETRIES - 1:
                    raise CatalogUnavailableError(
                        f"ListenBrainz returned {response.status_code}"
                    )
                await _sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                continue

            raise CatalogUnavailableError(
                f"ListenBrainz request to '{path}' failed with {response.status_code}"
            )

        raise CatalogUnavailableError("ListenBrainz did not respond after several attempts")

    async def get_recording_popularity(
        self, recording_mbids: list[str]
    ) -> dict[str, RecordingPopularity]:
        """Look up listen and listener counts for many recordings at once.

        Returns only the recordings ListenBrainz answered for. A missing id
        means no data rather than zero popularity, and the caller must keep that
        distinction.
        """
        results: dict[str, RecordingPopularity] = {}

        for start in range(0, len(recording_mbids), MAX_MBIDS_PER_CALL):
            chunk = recording_mbids[start : start + MAX_MBIDS_PER_CALL]
            payload = await self._post("popularity/recording", {"recording_mbids": chunk})

            for item in payload:
                mbid = item.get("recording_mbid")
                if not mbid:
                    continue
                results[mbid] = RecordingPopularity(
                    recording_mbid=mbid,
                    listen_count=item.get("total_listen_count"),
                    # ListenBrainz calls this total_user_count. It is the number
                    # of distinct people, which is the metric Audidle cares about.
                    listener_count=item.get("total_user_count"),
                )

        return results

    async def aclose(self) -> None:
        await self._http.aclose()


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
