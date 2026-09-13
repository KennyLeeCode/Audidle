"""Database schema for the curated song catalog.

This table is the answer to a limitation rather than a convenience: Spotify does
not expose stream counts or popularity to this app, so difficulty has to come
from data we hold ourselves. Each row pairs a real Spotify track id with a
stream estimate and the difficulty tier that estimate falls into.

Written against SQLAlchemy 2.0 with no SQLite specific types, so the same models
run on Postgres by changing the connection URL.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.enums import Difficulty


class Base(DeclarativeBase):
    pass


class CuratedSong(Base):
    """One curated song, with the popularity data Spotify will not give us.

    Metadata is denormalized onto this row on purpose. It is resolved once at
    ingest, which means song selection and the reveal need no Spotify call at
    all, so the game keeps working through a rate limit or an outage.
    """

    __tablename__ = "curated_songs"

    # The real Spotify track id, resolved at ingest. Primary key because it is
    # the identity everything else in the application keys on.
    track_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Recording identity, used by the guess matcher to accept a different
    # release of the same master.
    isrc: Mapped[str | None] = mapped_column(String(32), index=True, default=None)

    title: Mapped[str] = mapped_column(String(512))
    artist: Mapped[str] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512), default=None)
    artwork_url: Mapped[str | None] = mapped_column(Text, default=None)
    external_url: Mapped[str | None] = mapped_column(Text, default=None)
    release_year: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Comma separated. Spotify supplies none of these, so they are whatever the
    # seed file declares.
    genres: Mapped[str] = mapped_column(Text, default="")

    # -- Popularity, the reason this table exists ---------------------------

    stream_estimate: Mapped[int] = mapped_column(Integer, index=True)
    # Stored rather than derived on read, so a threshold change is a
    # reclassification job rather than a per-query computation.
    difficulty: Mapped[Difficulty] = mapped_column(String(16), index=True)
    # Where the figure came from, and how much to trust it. Both matter,
    # because these are estimates and should never be presented as exact.
    source: Mapped[str] = mapped_column(String(64), default="manual-estimate")
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    # When the figure was accurate. Stream counts only ever go up, so a stale
    # row drifts toward being too easy.
    streams_as_of: Mapped[str | None] = mapped_column(String(16), default=None)

    # -- Playability --------------------------------------------------------

    # Relative path to an audio file, when one is available. Null means this
    # song cannot currently be used in a round, and selection will skip it.
    audio_file: Mapped[str | None] = mapped_column(Text, default=None)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        # The query song selection runs on every round: eligible songs for a
        # tier that actually have audio.
        Index("ix_curated_difficulty_audio", "difficulty", "audio_file"),
    )

    def __repr__(self) -> str:
        return f"<CuratedSong {self.track_id} {self.title!r} by {self.artist!r}>"
