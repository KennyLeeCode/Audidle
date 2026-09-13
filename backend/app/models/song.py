"""Domain models for songs, their identifiers, and their audio sources.

Identity is the important idea here. A song's `id` is Audidle's own, and the
rest of the application keys on it without knowing or caring where the metadata
came from. Provider ids live in `external_ids` alongside each other, so Spotify,
MusicBrainz, and an ISRC are all just identifiers attached to one recording
rather than competing notions of what the song *is*.

Providers that have no catalog of their own, such as the mock and Spotify
catalogs, use their native id as the Audidle id. That is a deliberate shortcut
for those paths: they are search and enrichment sources, and the Audidle catalog
provider is the one that issues real internal ids.
"""

from dataclasses import dataclass, field

from app.models.enums import Difficulty, PlayableSourceKind

# -- Identifier vocabulary --------------------------------------------------
# Kept as constants rather than an enum so a new provider is a new string in one
# place and not a schema migration.

PROVIDER_AUDIDLE = "audidle"
PROVIDER_SPOTIFY = "spotify"
PROVIDER_MUSICBRAINZ = "musicbrainz"
PROVIDER_ISRC = "isrc"
PROVIDER_MOCK = "mock"

ID_TYPE_TRACK = "track_id"
ID_TYPE_RECORDING_MBID = "recording_mbid"
ID_TYPE_ARTIST_MBID = "artist_mbid"
ID_TYPE_ISRC = "isrc"


@dataclass(frozen=True)
class ExternalIdentifier:
    """One external id attached to a song.

    A song can carry several of these, including more than one ISRC, which is
    why they are a collection rather than columns on the song itself.
    """

    provider: str
    identifier_type: str
    identifier: str

    def matches(self, provider: str, identifier: str) -> bool:
        return self.provider == provider and self.identifier == identifier


@dataclass(frozen=True)
class Song:
    """One recording, as the rest of the application sees it.

    `id` is Audidle's identity for the recording. Guess comparison happens on it
    first, and falls back to the cross-provider tests in guess_matcher when a
    player picks a different release of the same song.
    """

    id: str
    title: str
    artist: str
    album: str | None = None
    artwork_url: str | None = None
    external_url: str | None = None
    release_year: int | None = None
    duration_ms: int | None = None
    explicit: bool = False
    genres: tuple[str, ...] = field(default_factory=tuple)
    external_ids: tuple[ExternalIdentifier, ...] = field(default_factory=tuple)

    @property
    def isrcs(self) -> tuple[str, ...]:
        """Every ISRC known for this recording.

        Plural because a recording legitimately carries more than one, for
        example when it is released in several territories.
        """
        return tuple(
            identifier.identifier
            for identifier in self.external_ids
            if identifier.identifier_type == ID_TYPE_ISRC
        )

    @property
    def isrc(self) -> str | None:
        """The primary ISRC, for the common single-value case."""
        found = self.isrcs
        return found[0] if found else None

    @property
    def musicbrainz_id(self) -> str | None:
        return self.external_id(PROVIDER_MUSICBRAINZ, ID_TYPE_RECORDING_MBID)

    @property
    def spotify_id(self) -> str | None:
        return self.external_id(PROVIDER_SPOTIFY, ID_TYPE_TRACK)

    def external_id(self, provider: str, identifier_type: str) -> str | None:
        for identifier in self.external_ids:
            if (
                identifier.provider == provider
                and identifier.identifier_type == identifier_type
            ):
                return identifier.identifier
        return None

    @property
    def decade(self) -> int | None:
        """Release decade, for the decade filter. 1997 becomes 1990."""
        return None if self.release_year is None else (self.release_year // 10) * 10

    @property
    def search_label(self) -> str:
        """Human readable label used in logs."""
        return f"{self.title} - {self.artist}"


@dataclass(frozen=True)
class SearchResult:
    """A song as offered to the player in the guess autocomplete.

    Carries the provider and the provider's own id, never Audidle's. That is a
    security property rather than a convenience: every song in our catalog has
    an Audidle id, so exposing one would tell the player which results are
    possible answers. Resolution back to an Audidle song happens server side
    when the guess is submitted.
    """

    provider: str
    external_id: str
    title: str
    artist: str
    album: str | None = None
    artwork_url: str | None = None


@dataclass(frozen=True)
class SongPopularity:
    """What a PopularityProvider knows about one song.

    stream_estimate is optional because most sources cannot supply one. Spotify
    exposes no stream counts at all, so this comes from our own data.
    """

    song_id: str
    difficulty: Difficulty
    stream_estimate: int | None = None
    popularity_score: int | None = None


@dataclass(frozen=True)
class PlayableSource:
    """A descriptor telling the frontend how to play a song.

    Deliberately not audio bytes. The backend says what kind of source this is
    and whether exact sub second clipping is possible, and the frontend picks a
    playback engine accordingly.

    supports_precise_clips is the important field. File sources are decodable,
    so Web Audio can schedule a 0.01s clip to the sample. Spotify's DRM player
    cannot, and the UI reports that honestly rather than pretending.
    """

    song_id: str
    kind: PlayableSourceKind
    # Relative URL the browser fetches for FILE_URL sources. Deliberately
    # opaque so the filename cannot leak the answer in devtools.
    url: str | None = None
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
    exclude_song_ids: frozenset[str] = frozenset()
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
        if song.id in self.exclude_song_ids:
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
