"""Song selection and playability.

Sits between GameService and the three providers. Its job is to answer one
question well: given a difficulty and a set of filters, hand back a song that
the player can actually hear.

That last part is the reason this class is not a one liner. A song can be
eligible on paper and still have no usable audio source, so selection retries
across candidates and only gives up after exhausting the configured attempts.
"""

import logging
import random

from app.core.errors import NoEligibleSongError, NoPlayableSongError
from app.models.song import PlayableSource, Song, SongSelectionCriteria
from app.providers.base import AudioProvider, PopularityProvider, SongCatalogProvider

logger = logging.getLogger(__name__)


class SongService:
    """Criteria based song selection with playability guarantees."""

    def __init__(
        self,
        catalog: SongCatalogProvider,
        popularity: PopularityProvider,
        audio: AudioProvider,
        max_attempts: int = 10,
        rng: random.Random | None = None,
    ) -> None:
        self._catalog = catalog
        self._popularity = popularity
        self._audio = audio
        self._max_attempts = max_attempts
        # Injectable so tests can make selection deterministic.
        self._rng = rng or random.Random()

    async def pick_playable_song(
        self, criteria: SongSelectionCriteria
    ) -> tuple[Song, PlayableSource]:
        """Draw a random eligible song that has a working audio source.

        Takes criteria rather than a bare difficulty so genre, decade, and
        explicit filters can be added later without changing this signature.

        Raises:
            NoEligibleSongError: nothing matched the difficulty and filters.
            NoPlayableSongError: candidates matched but none were playable.
        """
        candidates = await self._eligible_songs(criteria)

        if not candidates and criteria.exclude_track_ids:
            # Recent-song exclusion is best effort. Falling back to a possible
            # repeat beats telling the player there are no songs left.
            logger.info("recent song exclusion emptied the pool, relaxing it")
            relaxed = SongSelectionCriteria(
                difficulty=criteria.difficulty,
                genres=criteria.genres,
                decades=criteria.decades,
                release_year_min=criteria.release_year_min,
                release_year_max=criteria.release_year_max,
                explicit=criteria.explicit,
            )
            candidates = await self._eligible_songs(relaxed)

        if not candidates:
            raise NoEligibleSongError(
                f"no songs match difficulty '{criteria.difficulty.value}' with the active filters"
            )

        # Shuffle once and walk the list, so each attempt tries a different
        # song instead of possibly re-drawing one already known to be broken.
        self._rng.shuffle(candidates)

        attempted = 0
        for song in candidates:
            if attempted >= self._max_attempts:
                break
            attempted += 1

            source = await self._audio.get_playable_source(song.track_id)
            if source is None:
                logger.warning("song %s has no playable source, trying another", song.track_id)
                continue
            return song, source

        raise NoPlayableSongError(
            f"no playable audio source found after {attempted} attempts "
            f"for difficulty '{criteria.difficulty.value}'"
        )

    async def get_playable_source(self, track_id: str) -> PlayableSource | None:
        """Re-resolve a song's audio source, for example on round reload."""
        return await self._audio.get_playable_source(track_id)

    async def open_stream(self, track_id: str) -> tuple[bytes, str] | None:
        """Read raw audio bytes for the round scoped audio proxy route."""
        return await self._audio.open_stream(track_id)

    async def get_song(self, track_id: str) -> Song | None:
        return await self._catalog.get_song(track_id)

    async def search(self, query: str, limit: int = 10) -> list[Song]:
        return await self._catalog.search(query, limit=limit)

    async def _eligible_songs(self, criteria: SongSelectionCriteria) -> list[Song]:
        """Resolve criteria into concrete Song objects.

        Difficulty and exclusions are applied by the PopularityProvider, which
        owns the tier mapping. Metadata filters are applied here, where the
        Song objects actually exist.
        """
        track_ids = await self._popularity.get_eligible_track_ids(criteria)
        if not track_ids:
            return []
        songs = await self._catalog.get_songs(track_ids)
        return [song for song in songs if criteria.matches(song)]
