"""Move the existing curated songs into the Audidle catalog.

The old `curated_songs` table is keyed by Spotify track id and flattens
everything onto one row. This reads it and writes the same songs into the new
normalized tables, giving each one an Audidle id and turning its Spotify id and
ISRC into external identifiers.

Nothing is deleted. The curated table stays exactly as it was, so the old
provider path keeps working until the new one is proven.

Idempotent: a song is located by its Spotify identifier first, so running this
twice updates rather than duplicates.
"""

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.writer import (
    attach_genres,
    attach_identifier,
    attach_isrc,
    find_song_by_identifier,
    link_artist,
    recompute_eligibility,
    upsert_artist,
    upsert_audio_asset,
    upsert_popularity,
    upsert_song,
)
from app.database.models import CuratedSong
from app.models.song import ID_TYPE_TRACK, PROVIDER_SPOTIFY

logger = logging.getLogger(__name__)


class MigrationReport:
    """What the migration did, for the caller to print."""

    def __init__(self) -> None:
        self.total = 0
        self.created = 0
        self.updated = 0
        self.identifiers = 0
        self.isrcs = 0
        self.genres = 0
        self.popularity = 0
        self.audio = 0
        self.eligible = 0
        self.skipped: list[str] = []


async def migrate_curated_songs(
    session: AsyncSession, audio_dir: Path, rename_audio: bool = True
) -> MigrationReport:
    """Copy every curated row into the Audidle catalog.

    Audio files are renamed from the Spotify track id to the Audidle song id, so
    the asset reference matches the new identity. The rename is a copy followed
    by nothing: the original stays, because the old provider path still reads it.
    """
    report = MigrationReport()
    rows = (await session.execute(select(CuratedSong))).scalars().all()
    report.total = len(rows)

    for row in rows:
        existing = await find_song_by_identifier(session, PROVIDER_SPOTIFY, row.track_id)

        song = await upsert_song(
            session,
            song_id=existing.id if existing else None,
            title=row.title,
            artist_credit=row.artist,
            album=row.album,
            artwork_url=row.artwork_url,
            external_url=row.external_url,
            duration_ms=row.duration_ms,
            release_year=row.release_year,
            explicit=row.explicit,
        )
        if existing is None:
            report.created += 1
        else:
            report.updated += 1

        # -- Identity -------------------------------------------------------
        if await attach_identifier(
            session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, row.track_id
        ):
            report.identifiers += 1
        if row.isrc and await attach_isrc(session, song, row.isrc):
            report.isrcs += 1

        # -- Artists --------------------------------------------------------
        # The curated table stores one joined credit string, so it is split back
        # into parts here. Position zero is the primary artist.
        for position, name in enumerate(_split_credit(row.artist)):
            artist = await upsert_artist(session, name)
            await link_artist(session, song, artist, position=position)

        # -- Genres ---------------------------------------------------------
        names = [genre for genre in (row.genres or "").split(",") if genre.strip()]
        report.genres += await attach_genres(session, song, names, source="seed")

        # -- Popularity -----------------------------------------------------
        if row.stream_estimate is not None:
            await upsert_popularity(
                session,
                song,
                provider=row.source or "manual-estimate",
                stream_count=row.stream_estimate,
                confidence=row.confidence or "medium",
                measured_at=row.streams_as_of,
            )
            report.popularity += 1

        # -- Audio ----------------------------------------------------------
        if row.audio_file:
            reference = row.audio_file
            source_path = audio_dir / row.audio_file

            if rename_audio and source_path.is_file():
                # Name the asset after the Audidle id, since that is the
                # identity the new provider looks it up by.
                target = audio_dir / f"{song.id}{source_path.suffix}"
                if not target.exists():
                    target.write_bytes(source_path.read_bytes())
                reference = target.name

            playable = (audio_dir / reference).is_file()
            await upsert_audio_asset(
                session,
                song,
                provider="local",
                provider_reference=reference,
                duration_ms=row.duration_ms,
                playable=playable,
            )
            if playable:
                report.audio += 1

        eligibility = await recompute_eligibility(session, song)
        if eligibility.eligible:
            report.eligible += 1

    return report


def _split_credit(credit: str) -> list[str]:
    """Split a joined artist credit back into individual names.

    Conservative on purpose. An artist whose name genuinely contains one of
    these separators would be split wrongly, but the alternative is treating
    "Calvin Harris, Dua Lipa" as a single artist, which is worse and far more
    common.
    """
    parts: list[str] = []
    for chunk in credit.replace(" & ", ", ").replace(" x ", ", ").split(","):
        name = chunk.strip()
        if name and name not in parts:
            parts.append(name)
    return parts or [credit.strip()]
