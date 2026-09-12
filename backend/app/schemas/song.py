"""Wire schemas for songs.

Separate from the domain models in app/models so that what the API exposes is a
deliberate decision rather than an accident of the internal representation.
"""

from pydantic import BaseModel, Field

from app.models.enums import PlayableSourceKind
from app.models.song import PlayableSource, Song


class SongSummary(BaseModel):
    """A song as shown in search results.

    Safe to return at any time. Search must never mark or omit the round's
    answer, because either would identify it visually.
    """

    track_id: str = Field(description="Canonical id, submitted as a guess")
    title: str
    artist: str
    album: str | None = None
    artwork_url: str | None = None

    @classmethod
    def from_domain(cls, song: Song) -> "SongSummary":
        return cls(
            track_id=song.track_id,
            title=song.title,
            artist=song.artist,
            album=song.album,
            artwork_url=song.artwork_url,
        )


class SongDetail(SongSummary):
    """Full song metadata. Only ever returned after a round has ended."""

    release_year: int | None = None
    duration_ms: int | None = None
    explicit: bool = False
    genres: list[str] = Field(default_factory=list)
    external_url: str | None = Field(
        default=None, description="Link out to the track, for example on Spotify"
    )

    @classmethod
    def from_domain(cls, song: Song) -> "SongDetail":
        return cls(
            track_id=song.track_id,
            title=song.title,
            artist=song.artist,
            album=song.album,
            artwork_url=song.artwork_url,
            release_year=song.release_year,
            duration_ms=song.duration_ms,
            explicit=song.explicit,
            genres=list(song.genres),
            external_url=song.external_url,
        )


class AudioSourceResponse(BaseModel):
    """How the browser should play this round's audio.

    Deliberately carries no song identity. The url is a round scoped proxy
    route, not a filename, so devtools cannot reveal the answer.

    supports_precise_clips tells the frontend which playback engine to use.
    True means the source is fully decodable and Web Audio can schedule a clip
    to the exact sample, which is what makes the 0.01s stage real. False means
    the frontend falls back to timer based clipping and reports the timing as
    approximate rather than pretending otherwise.
    """

    kind: PlayableSourceKind
    url: str | None = None
    spotify_uri: str | None = None
    duration_ms: int | None = None
    supports_precise_clips: bool

    @classmethod
    def from_domain(cls, source: PlayableSource, url: str | None = None) -> "AudioSourceResponse":
        return cls(
            kind=source.kind,
            url=url if url is not None else source.url,
            spotify_uri=source.spotify_uri,
            duration_ms=source.duration_ms,
            supports_precise_clips=source.supports_precise_clips,
        )


class SearchResponse(BaseModel):
    """Autocomplete payload."""

    query: str
    results: list[SongSummary]
