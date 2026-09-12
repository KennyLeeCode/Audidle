"""Provider ports.

Three abstract boundaries separate the game from the outside world. Everything
above this layer depends on these interfaces only, so swapping the mock catalog
for Spotify, or the mock audio source for a real one, touches exactly one
factory function in dependencies.py.

The split into three ports rather than one is deliberate, and it is driven by a
real constraint: Spotify is an excellent catalog and search provider, it cannot
tell you stream counts, and it cannot give you arbitrary raw audio. Fusing
those responsibilities into a single SpotifyProvider would bake that mismatch
into the architecture.
"""

from abc import ABC, abstractmethod

from app.models.enums import Difficulty
from app.models.song import (
    PlayableSource,
    Song,
    SongPopularity,
    SongSelectionCriteria,
)


class SongCatalogProvider(ABC):
    """Identity and metadata. Answers "what songs exist and what are they".

    Implemented by MockSongProvider now and SpotifySongProvider later.
    """

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[Song]:
        """Return songs matching a free text query, best match first."""

    @abstractmethod
    async def get_song(self, track_id: str) -> Song | None:
        """Return one song by canonical track id, or None if unknown."""

    @abstractmethod
    async def get_songs(self, track_ids: list[str]) -> list[Song]:
        """Batch lookup. Unknown ids are omitted rather than raising."""


class PopularityProvider(ABC):
    """Difficulty. Answers "how well known is this, and which tier is it in".

    Separate from the catalog because Spotify's Web API does not expose stream
    counts. An implementation may be backed by a curated dataset with real
    stream figures, or approximate from Spotify's 0-100 popularity score.
    """

    @abstractmethod
    async def get_popularity(self, track_id: str) -> SongPopularity | None:
        """Return the popularity record for a track, or None if unknown."""

    @abstractmethod
    async def get_eligible_track_ids(self, criteria: SongSelectionCriteria) -> list[str]:
        """Return every track id eligible for the given difficulty.

        Only the difficulty and exclusion parts of the criteria are honoured
        here. Metadata filters such as genre and decade are applied by
        SongService, which has the Song objects to test them against.
        """

    @abstractmethod
    async def get_difficulty(self, track_id: str) -> Difficulty | None:
        """Return which tier a track belongs to."""


class AudioProvider(ABC):
    """Playback. Answers "how does the browser actually hear this track".

    Returns a descriptor rather than audio bytes. See PlayableSource for why
    that distinction matters to the 0.01s stage.
    """

    @abstractmethod
    async def get_playable_source(self, track_id: str) -> PlayableSource | None:
        """Return a playable source descriptor, or None if the track has none.

        Returning None is a normal outcome, not an error. SongService uses it
        to reject a candidate and draw another, so a player is never handed a
        round with no audio.
        """

    @abstractmethod
    async def open_stream(self, track_id: str) -> tuple[bytes, str] | None:
        """Return raw audio bytes and a MIME type for file backed sources.

        Used by the audio proxy route, which serves audio under an opaque
        round-scoped URL so the filename cannot reveal the answer. Providers
        that do not serve bytes (Spotify SDK) return None.
        """
