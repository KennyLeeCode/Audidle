"""Importing discovered candidates into the catalog.

The rule this whole module exists to honour: a song may enter the catalog with
no popularity at all. It gets identity, metadata, and a row, and it stays
ineligible until a popularity provider has something real to say about it.
Nothing here writes a stream count, a score, or a difficulty.

Deduplication runs strongest key first, exactly as the resolver does elsewhere:

    1. an existing Audidle song already carrying this provider id
    2. ISRC
    3. MusicBrainz recording id
    4. normalized title and artist, and only as a fallback

The fallback is last for a reason. Two artists genuinely release different songs
under the same name, so text alone would merge them, and a merged song is far
harder to unpick than a duplicate is to spot.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.writer import (
    attach_identifier,
    attach_isrc,
    find_song_by_identifier,
    link_artist,
    recompute_eligibility,
    upsert_artist,
    upsert_song,
)
from app.database.catalog_models import CatalogSong
from app.models.song import (
    ID_TYPE_RECORDING_MBID,
    PROVIDER_MUSICBRAINZ,
)
from app.providers.seed.base import SongCandidate
from app.services.guess_matcher import normalize_artist, normalize_title

logger = logging.getLogger(__name__)


@dataclass
class ExpansionReport:
    considered: int = 0
    created: int = 0
    duplicate_provider_id: int = 0
    duplicate_isrc: int = 0
    duplicate_mbid: int = 0
    duplicate_metadata: int = 0
    failed: int = 0
    created_titles: list[str] = field(default_factory=list)

    @property
    def duplicates(self) -> int:
        return (
            self.duplicate_provider_id
            + self.duplicate_isrc
            + self.duplicate_mbid
            + self.duplicate_metadata
        )


async def _existing_by_metadata(
    session: AsyncSession, candidate: SongCandidate
) -> CatalogSong | None:
    """Last resort match on normalized title and artist.

    Deliberately *not* the canonical key the autocomplete groups by. That key
    folds "Song - Acoustic" into "Song", which is right for choosing one
    suggestion to show a player and wrong for deciding what the catalog holds:
    an acoustic version is a different recording and belongs in the catalog
    alongside the original. Autocomplete hides it, storage keeps it.

    Normalization still folds pure release wording, so a remaster does not
    become a second copy of the same recording.
    """
    wanted_title = normalize_title(candidate.title)
    wanted_artist = normalize_artist(candidate.artist)
    if not wanted_title:
        return None

    # Narrow on the first words of the normalized title, so the comparison set
    # stays small without the LIKE having to match punctuation exactly.
    prefix = " ".join(wanted_title.split()[:3])

    rows = (
        await session.execute(
            select(CatalogSong).where(CatalogSong.title.ilike(f"%{prefix}%"))
        )
    ).scalars().all()

    for row in rows:
        if (
            normalize_title(row.title) == wanted_title
            and normalize_artist(row.artist_credit) == wanted_artist
        ):
            return row
    return None


async def find_existing(
    session: AsyncSession, candidate: SongCandidate
) -> tuple[CatalogSong | None, str]:
    """Locate an existing song for a candidate, strongest key first."""
    if candidate.source_id:
        found = await find_song_by_identifier(session, candidate.source, candidate.source_id)
        if found:
            return found, "provider_id"

    if candidate.isrc:
        from app.models.song import PROVIDER_ISRC

        found = await find_song_by_identifier(
            session, PROVIDER_ISRC, candidate.isrc.strip().upper()
        )
        if found:
            return found, "isrc"

    if candidate.musicbrainz_recording_id:
        found = await find_song_by_identifier(
            session, PROVIDER_MUSICBRAINZ, candidate.musicbrainz_recording_id
        )
        if found:
            return found, "mbid"

    found = await _existing_by_metadata(session, candidate)
    if found:
        return found, "metadata"

    return None, "new"


async def import_candidates(
    session: AsyncSession,
    candidates: list[SongCandidate],
    max_new: int | None = None,
    progress=None,
) -> ExpansionReport:
    """Create catalog songs for candidates that are genuinely new.

    Songs are created with identity and nothing else. No popularity row is
    written, so eligibility comes out false and the song waits for a real
    measurement rather than a guessed one.

    `max_new` caps how many songs are created. Callers over-discover on purpose,
    because most candidates turn out to be duplicates, and without a cap the
    surplus lands in the catalog and overshoots the batch size.
    """
    report = ExpansionReport()

    for index, candidate in enumerate(candidates, start=1):
        if max_new is not None and report.created >= max_new:
            break
        report.considered += 1

        try:
            existing, how = await find_existing(session, candidate)

            if existing is not None:
                setattr(report, f"duplicate_{how}", getattr(report, f"duplicate_{how}") + 1)
                # Still worth attaching any identifier the candidate carries
                # that the existing song lacks, since that strengthens future
                # deduplication.
                if candidate.musicbrainz_recording_id:
                    await attach_identifier(
                        session,
                        existing,
                        PROVIDER_MUSICBRAINZ,
                        ID_TYPE_RECORDING_MBID,
                        candidate.musicbrainz_recording_id,
                    )
                if candidate.isrc:
                    await attach_isrc(session, existing, candidate.isrc)
                continue

            song = await upsert_song(
                session,
                song_id=None,
                title=candidate.title,
                artist_credit=candidate.artist,
                release_year=candidate.release_year,
            )

            if candidate.musicbrainz_recording_id:
                await attach_identifier(
                    session,
                    song,
                    PROVIDER_MUSICBRAINZ,
                    ID_TYPE_RECORDING_MBID,
                    candidate.musicbrainz_recording_id,
                )
            if candidate.isrc:
                await attach_isrc(session, song, candidate.isrc)

            artist = await upsert_artist(
                session, candidate.artist, musicbrainz_artist_id=candidate.musicbrainz_artist_id
            )
            await link_artist(session, song, artist, position=0)

            # No popularity is written. Eligibility therefore resolves to false,
            # which is the correct state for a song we know of but cannot yet
            # rank or play.
            await recompute_eligibility(session, song)

            report.created += 1
            report.created_titles.append(candidate.label)

        except Exception:
            report.failed += 1
            logger.exception("could not import %s", candidate.label)

        if progress and index % 25 == 0:
            progress(index, len(candidates))

    await session.commit()
    return report
