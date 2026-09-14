"""Candidates from the discographies of artists we already hold.

The cheapest useful expansion available. Every artist in the catalog already has
a MusicBrainz id, and ListenBrainz will list their recordings with those ids
attached, free and unauthenticated. That gives candidates with strong identity
from the start, so deduplication never has to fall back to matching text.

The listen counts it returns are used to order candidates, so the better known
recordings arrive first. They are deliberately not carried onto the candidate:
ListenBrainz skews heavily toward album and indie listening, and letting those
numbers reach the catalog as popularity is exactly the fabrication this design
avoids.
"""

import logging

from app.providers.listenbrainz.listenbrainz_provider import ListenBrainzProvider
from app.providers.seed.base import CatalogSeedProvider, SongCandidate

logger = logging.getLogger(__name__)


class ListenBrainzArtistSeedProvider(CatalogSeedProvider):
    """Expands the catalog along the artists already in it."""

    def __init__(
        self,
        provider: ListenBrainzProvider,
        artists: list[tuple[str, str]],
        per_artist: int = 5,
    ) -> None:
        # (artist name, MusicBrainz artist id)
        self._artists = artists
        self._provider = provider
        self._per_artist = per_artist

    @property
    def name(self) -> str:
        return "listenbrainz-artist"

    async def discover(self, limit: int) -> list[SongCandidate]:
        """Walk the artists in rounds, taking a few recordings from each.

        Round robin rather than artist by artist, so a target of sixty songs
        spreads across sixty artists instead of exhausting one discography. A
        catalog that grows in breadth stays more playable than one that grows
        in depth.
        """
        per_artist: dict[str, list[SongCandidate]] = {}

        for name, artist_mbid in self._artists:
            try:
                recordings = await self._provider.get_top_recordings_for_artist(artist_mbid)
            except Exception:
                logger.exception("discovery failed for %s", name)
                continue

            found: list[SongCandidate] = []
            for recording in recordings:
                title = (recording.get("recording_name") or "").strip()
                mbid = recording.get("recording_mbid")
                if not title or not mbid:
                    continue

                found.append(
                    SongCandidate(
                        title=title,
                        artist=name,
                        musicbrainz_recording_id=mbid,
                        musicbrainz_artist_id=artist_mbid,
                        source=self.name,
                        source_id=mbid,
                    )
                )
                if len(found) >= self._per_artist:
                    break

            if found:
                per_artist[artist_mbid] = found

        candidates: list[SongCandidate] = []
        for depth in range(self._per_artist):
            for found in per_artist.values():
                if depth < len(found):
                    candidates.append(found[depth])
                    if len(candidates) >= limit:
                        return candidates

        return candidates
