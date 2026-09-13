"""Wire schemas for songs.

Separate from the domain models in app/models so that what the API exposes is a
deliberate decision rather than an accident of the internal representation.
"""

from pydantic import BaseModel, Field

from app.models.enums import PlayableSourceKind
from app.models.song import ID_TYPE_TRACK, PROVIDER_SPOTIFY, PlayableSource, Song


class SongSummary(BaseModel):
    """A song as shown in search results.

    Carries the provider and that provider's own id, never Audidle's song id.
    Every song in our catalog has an Audidle id, so returning one would tell the
    player which results are possible answers. Resolution back to an Audidle
    song happens server side when the guess is submitted.

    Search must also never mark or omit the round's answer, since either would
    identify it visually.
    """

    provider: str = Field(description="Which catalog this result came from")
    external_id: str = Field(description="That provider's id, submitted as a guess")
    title: str
    artist: str
    album: str | None = None
    artwork_url: str | None = None

    @classmethod
    def from_domain(cls, song: Song, provider: str) -> "SongSummary":
        return cls(
            provider=provider,
            external_id=song.external_id(provider, ID_TYPE_TRACK) or song.id,
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
    isrc: str | None = None
    external_url: str | None = Field(
        default=None, description="Link out to the track, for example on Spotify"
    )

    @classmethod
    def from_domain(cls, song: Song, provider: str = PROVIDER_SPOTIFY) -> "SongDetail":
        return cls(
            provider=provider,
            external_id=song.external_id(provider, ID_TYPE_TRACK) or song.id,
            title=song.title,
            artist=song.artist,
            album=song.album,
            artwork_url=song.artwork_url,
            release_year=song.release_year,
            duration_ms=song.duration_ms,
            explicit=song.explicit,
            genres=list(song.genres),
            isrc=song.isrc,
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
