"""The Audidle catalog schema.

This is the database the game reads from. Providers write into it during
ingestion and enrichment, and the selection path never leaves it.

Two ideas shape the layout:

**Identity is ours.** A song's primary key is an Audidle UUID. Spotify track
ids, MusicBrainz recording ids, and ISRCs are all rows in `external_identifiers`
pointing at it. No provider owns the notion of what a song is, and adding a
provider is a new string value rather than a migration.

**Knowing about a song is not the same as being able to play it.** Ingest
produces rows with identity and metadata and usually no audio. `song_eligibility`
is the explicit gate between the catalog and the game pool, so "we have 50,000
recordings" and "3,000 can be used in a round" stay separate facts.

SQLite today. No SQLite specific types are used, so moving to Postgres is a
connection URL change.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models import Base
from app.models.enums import Difficulty


def _now() -> datetime:
    return datetime.now(UTC)


class CatalogSong(Base):
    """One recording in the Audidle catalog.

    Holds only what is true regardless of provider. Anything that belongs to a
    particular provider's view lives in song_provider_metadata instead, so a
    change to Spotify's response shape cannot reach this table.
    """

    __tablename__ = "songs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    # Denormalized display credit, for example "Calvin Harris, Dua Lipa". The
    # structured version is in song_artists. Both exist because the game needs
    # a label to show and the resolver needs the parts.
    artist_credit: Mapped[str] = mapped_column(String(512), index=True)
    album: Mapped[str | None] = mapped_column(String(512), default=None)
    artwork_url: Mapped[str | None] = mapped_column(Text, default=None)
    external_url: Mapped[str | None] = mapped_column(Text, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    release_year: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    identifiers: Mapped[list["ExternalIdentifierRow"]] = relationship(
        back_populates="song", cascade="all, delete-orphan", lazy="selectin"
    )
    genres: Mapped[list["SongGenre"]] = relationship(
        back_populates="song", cascade="all, delete-orphan", lazy="selectin"
    )
    artists: Mapped[list["SongArtist"]] = relationship(
        back_populates="song", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<CatalogSong {self.id} {self.title!r} by {self.artist_credit!r}>"


class Artist(Base):
    """A credited artist, with its MusicBrainz identity when known."""

    __tablename__ = "artists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), index=True)
    # Accent folded and lowercased, so "Beyoncé" and "Beyonce" are one artist.
    # Stored rather than computed, because a LIKE on the display name is itself
    # accent sensitive and would never match across the two spellings.
    normalized_name: Mapped[str] = mapped_column(String(512), index=True, default="")
    sort_name: Mapped[str | None] = mapped_column(String(512), default=None)
    musicbrainz_artist_id: Mapped[str | None] = mapped_column(
        String(36), unique=True, index=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SongArtist(Base):
    """Join between a song and its credited artists, in credit order."""

    __tablename__ = "song_artists"

    song_id: Mapped[str] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), primary_key=True
    )
    artist_id: Mapped[str] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True
    )
    # Zero is the primary artist. Matching only compares position zero, since
    # featured credits appear inconsistently between releases.
    position: Mapped[int] = mapped_column(Integer, default=0)
    credited_name: Mapped[str | None] = mapped_column(String(512), default=None)

    song: Mapped[CatalogSong] = relationship(back_populates="artists")
    artist: Mapped[Artist] = relationship(lazy="selectin")


class ExternalIdentifierRow(Base):
    """One external id attached to a song.

    Normalized rather than columns, because a recording legitimately carries
    several ISRCs, and because a new provider should not need a schema change.

    The unique constraint is what makes ingestion idempotent and safe: the same
    Spotify track id can only ever point at one Audidle song, so re-running an
    import cannot silently fork a song into two.
    """

    __tablename__ = "external_identifiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[str] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    identifier_type: Mapped[str] = mapped_column(String(32))
    identifier: Mapped[str] = mapped_column(String(255), index=True)
    # How this identifier came to be attached. See MatchConfidence.
    confidence: Mapped[str] = mapped_column(String(32), default="exact_provider_id")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    song: Mapped[CatalogSong] = relationship(back_populates="identifiers")

    __table_args__ = (
        UniqueConstraint(
            "provider", "identifier_type", "identifier", name="uq_external_identifier"
        ),
        Index("ix_identifier_lookup", "provider", "identifier"),
    )


class SongProviderMetadata(Base):
    """Raw provider payloads, kept separate from canonical metadata.

    Lets us hold on to whatever a provider returned without the songs table
    depending on that provider's response shape, and gives enrichment a
    last_synced_at to decide what is stale.
    """

    __tablename__ = "song_provider_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[str] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(255), default=None)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("song_id", "provider", name="uq_song_provider"),)


class Genre(Base):
    """A canonical genre.

    The slug is the identity, so "Hip Hop", "hip-hop", and "hip hop" collapse
    into one row. Genuinely different subgenres keep their own rows.
    """

    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)


class SongGenre(Base):
    """Join between a song and a genre, recording where the tag came from."""

    __tablename__ = "song_genres"

    song_id: Mapped[str] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), primary_key=True
    )
    genre_id: Mapped[int] = mapped_column(
        ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True
    )
    # "musicbrainz", "seed", and so on. Kept so a bad source can be revoked.
    source: Mapped[str] = mapped_column(String(32), default="seed")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    song: Mapped[CatalogSong] = relationship(back_populates="genres")
    genre: Mapped[Genre] = relationship(lazy="selectin")


class SongPopularityRow(Base):
    """A popularity measurement for a song.

    Several rows per song are allowed, one per provider, so a real chart figure
    can sit alongside an estimate without either overwriting the other.
    measured_at matters because stream counts only ever go up, so an old figure
    drifts toward making a song look harder than it is.
    """

    __tablename__ = "song_popularity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[str] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    stream_count: Mapped[int | None] = mapped_column(Integer, default=None)
    popularity_score: Mapped[int | None] = mapped_column(Integer, default=None)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    measured_at: Mapped[str | None] = mapped_column(String(16), default=None)
    source_reference: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("song_id", "provider", name="uq_song_popularity_provider"),
    )


class SongPopularitySignal(Base):
    """One raw popularity measurement from one source.

    Deliberately append-friendly and never overwritten by the derived score.
    Raw view counts are the expensive thing to collect, and the scoring formula
    is the cheap thing to change, so keeping the measurements lets difficulty be
    recalculated later without re-spending API quota.

    Stored as (source, metric, value) rather than named columns so adding
    Last.fm play counts or ListenBrainz listeners needs no migration.
    """

    __tablename__ = "song_popularity_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[str] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    # "youtube", "listenbrainz", "manual-estimate"
    source: Mapped[str] = mapped_column(String(32), index=True)
    # "view_count", "listen_count", "listener_count", "stream_estimate"
    metric: Mapped[str] = mapped_column(String(32))
    value: Mapped[int] = mapped_column(BigInteger)
    # Which video, recording, or dataset row the number came from.
    source_reference: Mapped[str | None] = mapped_column(Text, default=None)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        # One current value per source and metric. History would be better but
        # is not needed yet, and this keeps refreshes idempotent.
        UniqueConstraint("song_id", "source", "metric", name="uq_song_signal"),
        Index("ix_signal_lookup", "source", "metric"),
    )


class AudioAsset(Base):
    """A playable audio file for a song.

    Separate from the song because a recording can exist in the catalog with no
    audio at all, which is the normal case after ingestion. `playable` is set by
    validation rather than assumed, so a row pointing at a missing or corrupt
    file does not keep a song in the game pool.
    """

    __tablename__ = "audio_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[str] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="local")
    # A filename for local audio. Meaning is provider defined.
    provider_reference: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    playable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Generated test audio, not the real recording. Tracked explicitly so it is
    # never counted as real audio in statistics and can be excluded from the
    # game pool by configuration.
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("song_id", "provider", "provider_reference", name="uq_audio_asset"),
    )


class SongEligibility(Base):
    """Whether a song can currently be used in a round, and why.

    A materialized table rather than a view or a computed predicate, because the
    selection query runs on every round and needs one indexed read on
    (difficulty, eligible) instead of a five way join. Recomputed by the
    pipeline whenever anything feeding it changes.

    The individual flags are kept rather than just the final boolean, so the
    validator can report *which* requirement a song is failing.
    """

    __tablename__ = "song_eligibility"

    song_id: Mapped[str] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), primary_key=True
    )
    has_identity: Mapped[bool] = mapped_column(Boolean, default=False)
    has_difficulty: Mapped[bool] = mapped_column(Boolean, default=False)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_playable: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Derived from popularity plus the configurable thresholds, so a threshold
    # change is a recompute rather than a schema concern.
    difficulty: Mapped[Difficulty | None] = mapped_column(
        String(16), index=True, default=None
    )
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (Index("ix_eligible_difficulty", "eligible", "difficulty"),)


class UnresolvedMatch(Base):
    """A provider match that was not confident enough to accept.

    The alternative to guessing. A weak match is parked here for review instead
    of being written into the catalog, because a wrong merge is much harder to
    undo than an unmatched song is to revisit.
    """

    __tablename__ = "unresolved_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[str | None] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True, default=None
    )
    provider: Mapped[str] = mapped_column(String(32))
    candidate_payload: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(32), default="unresolved")
    # pending, accepted, or rejected.
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
