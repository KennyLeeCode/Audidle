"""Domain models for songs and their audio sources.

These are provider agnostic on purpose. A Spotify track, a local file, and a
future third provider all normalize into Song, so nothing above the provider
layer ever needs to know where a song came from.
"""

from dataclasses import dataclass, field

from app.models.enums import Difficulty, PlayableSourceKind


@dataclass(frozen=True)
class Song:
    """One track, as the rest of the application sees it.

    track_id is the canonical identity used for guess comparison. Guesses are
    always compared on this field and never on title strings, which is what
    keeps "Blinding Lights" and "Blinding Lights - Single Version" from being
    treated as the same answer.
    """

    track_id: str
    title: str
    artist: str
    # International Standard Recording Code. Identifies the *recording* rather
    # than the release, so the album cut and the single of the same master
    # share one ISRC where their track ids differ. This is what makes guess
    # matching work across duplicate catalog entries.
    isrc: str | None = None
    album: str | None = None
    artwork_url: str | None = None
    external_url: str | None = None
    release_year: int | None = None
    duration_ms: int | None = None
    explicit: bool = False
    genres: tuple[str, ...] = field(default_factory=tuple)

    @property
    def decade(self) -> int | None:
        """Release decade, for the decade filter. 1997 becomes 1990."""
        return None if self.release_year is None else (self.release_year // 10) * 10

    @property
    def search_label(self) -> str:
        """Human readable label used in search results and logs."""
        return f"{self.title} - {self.artist}"


@dataclass(frozen=True)
class SongPopularity:
    """What a PopularityProvider knows about one song.

    stream_estimate is optional because Spotify's Web API does not expose
    stream counts. A dataset backed provider fills it in, a popularity-score
    based provider leaves it None and supplies popularity_score instead.
    """

    track_id: str
    difficulty: Difficulty
    stream_estimate: int | None = None
    popularity_score: int | None = None


@dataclass(frozen=True)
class PlayableSource:
    """A descriptor telling the frontend how to play a song.

    Deliberately not audio bytes. The backend says what kind of source this is
    and whether exact sub second clipping is possible, and the frontend picks a
    playback engine accordingly. Swapping audio backends later changes what
    this struct contains, and changes nothing in the game rules.

    supports_precise_clips is the important field. File sources are decodable,
    so Web Audio can schedule a 0.01s clip to the sample. Spotify's DRM player
    cannot, and the UI reports that honestly rather than pretending.
    """

    track_id: str
    kind: PlayableSourceKind
    # Relative URL the browser fetches for FILE_URL sources. Deliberately
    # opaque so the filename cannot leak the answer in devtools.
    url: str | None = None
    # Spotify URI for SPOTIFY_SDK sources.
    spotify_uri: str | None = None
    duration_ms: int | None = None
    supports_precise_clips: bool = False


@dataclass(frozen=True)
class SongSelectionCriteria:
    """Everything that narrows which song a round may draw.

    Selection takes this object rather than a bare difficulty so that adding
    the genre, decade, and explicit filters later is a new field plus a new
    predicate, not a signature change rippling through every caller.
    """

    difficulty: Difficulty
    exclude_track_ids: frozenset[str] = frozenset()
    genres: tuple[str, ...] | None = None
    decades: tuple[int, ...] | None = None
    release_year_min: int | None = None
    release_year_max: int | None = None
    explicit: bool | None = None

    def matches(self, song: Song) -> bool:
        """Whether a song satisfies every non difficulty filter.

        Difficulty is applied by the PopularityProvider, which owns the
        song-to-tier mapping, so it is not re-checked here.
        """
        if song.track_id in self.exclude_track_ids:
            return False
        if self.genres and not set(self.genres) & set(song.genres):
            return False
        if self.decades and song.decade not in self.decades:
            return False
        if self.release_year_min is not None:
            if song.release_year is None or song.release_year < self.release_year_min:
                return False
        if self.release_year_max is not None:
            if song.release_year is None or song.release_year > self.release_year_max:
                return False
        if self.explicit is not None and song.explicit != self.explicit:
            return False
        return True
