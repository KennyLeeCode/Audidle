"""SongCatalogProvider backed by the Spotify Web API.

Provides identity and metadata only. That is not a design compromise, it is what
the API actually offers: this app's credentials return track metadata and
nothing else. Verified against the live API rather than assumed:

    search, tracks, artists (basic)  ->  200
    audio-features, audio-analysis   ->  403
    related-artists, top-tracks      ->  403
    recommendations                  ->  404

and a track object does not merely have a null `preview_url` and `popularity`,
those keys are absent from the response entirely. Spotify removed them for apps
created after November 2024.

The consequences, made explicit so nothing downstream assumes otherwise:

  - No audio. Audio comes from AudioProvider, never from here.
  - No popularity or stream counts. Difficulty comes from PopularityProvider,
    which needs its own dataset.
  - No genres on tracks. Artist genres are also unavailable to this app, so the
    genre filter cannot be driven by Spotify either.
"""

import logging
from typing import Any

from app.models.song import (
    ID_TYPE_ISRC,
    ID_TYPE_TRACK,
    PROVIDER_ISRC,
    PROVIDER_SPOTIFY,
    ExternalIdentifier,
    Song,
)
from app.providers.base import SongCatalogProvider
from app.providers.spotify.spotify_client import SpotifyClient

logger = logging.getLogger(__name__)

# Spotify's batch track endpoint accepts at most 50 ids per call.
MAX_BATCH_SIZE = 50
# Measured against this app's credentials, not taken from the documentation.
# The docs say 50, but anything above 10 returns HTTP 400 for an app created
# after Spotify's November 2024 restrictions, even when the result set is
# larger: a query reporting total=15 still refuses limit=11. Requests are
# clamped here so callers can ask for more without triggering a 400.
MAX_SEARCH_LIMIT = 10


class SpotifySongProvider(SongCatalogProvider):
    """Catalog provider reading from Spotify."""

    def __init__(self, client: SpotifyClient, market: str | None = None) -> None:
        self._client = client
        # Restricting to a market filters out tracks unavailable there, which
        # keeps search results consistent with what a player would recognize.
        self._market = market

    @property
    def provider_name(self) -> str:
        return "spotify"

    async def search(self, query: str, limit: int = 10) -> list[Song]:
        params: dict[str, Any] = {
            "q": query,
            "type": "track",
            "limit": min(limit, MAX_SEARCH_LIMIT),
        }
        if self._market:
            params["market"] = self._market

        payload = await self._client.get("search", params=params)
        items = payload.get("tracks", {}).get("items", [])
        return [song for song in (self._to_song(item) for item in items) if song]

    async def get_song(self, song_id: str) -> Song | None:
        params = {"market": self._market} if self._market else None
        payload = await self._client.get(f"tracks/{song_id}", params=params)
        # An empty dict is the client's translation of a 404.
        return self._to_song(payload) if payload else None

    async def get_songs(self, song_ids: list[str]) -> list[Song]:
        """Batch lookup, chunked to Spotify's 50 id limit.

        Unknown ids come back as nulls in the response and are dropped, matching
        the contract that this method omits rather than raises.
        """
        songs: list[Song] = []

        for start in range(0, len(song_ids), MAX_BATCH_SIZE):
            chunk = song_ids[start : start + MAX_BATCH_SIZE]
            params: dict[str, Any] = {"ids": ",".join(chunk)}
            if self._market:
                params["market"] = self._market

            payload = await self._client.get("tracks", params=params)
            for item in payload.get("tracks", []) or []:
                song = self._to_song(item)
                if song:
                    songs.append(song)

        return songs

    # -- Mapping --------------------------------------------------------------

    def _to_song(self, item: dict[str, Any] | None) -> Song | None:
        """Map a Spotify track object onto the domain model.

        Defensive throughout. Missing artwork, a missing album, and an unparsable
        release date are all normal in real catalog data, and none of them should
        cost the player a round.
        """
        if not item or not item.get("id"):
            return None

        album = item.get("album") or {}
        isrc = (item.get("external_ids") or {}).get("isrc")

        identifiers = [ExternalIdentifier(PROVIDER_SPOTIFY, ID_TYPE_TRACK, item["id"])]
        if isrc:
            identifiers.append(ExternalIdentifier(PROVIDER_ISRC, ID_TYPE_ISRC, isrc))

        try:
            return Song(
                # Spotify has no Audidle id to give, so its own id stands in.
                # The Audidle catalog provider issues real internal ids.
                id=item["id"],
                title=item.get("name") or "Unknown title",
                artist=self._join_artists(item.get("artists")),
                external_ids=tuple(identifiers),
                album=album.get("name"),
                artwork_url=self._pick_artwork(album.get("images")),
                external_url=(item.get("external_urls") or {}).get("spotify"),
                release_year=self._parse_year(album.get("release_date")),
                duration_ms=item.get("duration_ms"),
                explicit=bool(item.get("explicit", False)),
                # Spotify does not expose track genres, and artist genres are
                # unavailable to this app. Left empty rather than guessed.
                genres=(),
            )
        except Exception:
            logger.exception("could not map Spotify track %s", item.get("id"))
            return None

    @staticmethod
    def _join_artists(artists: list[dict[str, Any]] | None) -> str:
        """Render collaborations as one artist string, matching how they read."""
        names = [artist.get("name", "") for artist in (artists or []) if artist.get("name")]
        return ", ".join(names) if names else "Unknown artist"

    @staticmethod
    def _pick_artwork(images: list[dict[str, Any]] | None) -> str | None:
        """Pick a mid sized cover, falling back to whatever exists.

        Spotify returns images largest first. The 300px variant suits both the
        search row and the reveal card, so downloading the 640px one is waste.
        """
        if not images:
            return None
        for image in images:
            if image.get("width") and 200 <= image["width"] <= 400:
                return image.get("url")
        return images[-1].get("url")

    @staticmethod
    def _parse_year(release_date: str | None) -> int | None:
        """Extract the year from a release date.

        Precision varies between "2020-03-20", "2020-03", and "2020", so only
        the leading year is read.
        """
        if not release_date:
            return None
        try:
            return int(release_date[:4])
        except ValueError:
            return None
