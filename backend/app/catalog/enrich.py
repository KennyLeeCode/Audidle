"""Enrichment passes over the catalog.

Each pass takes songs we already hold and attaches information from one
provider. They are separate from ingestion because they run on different
schedules and at different rates: MusicBrainz is one request per second, Spotify
is fast, and neither should block the other.

Every pass is **resumable**, which is not optional at MusicBrainz's rate limit.
A 5,000 song enrichment is well over an hour, and it will be interrupted. The
mechanism is simple and has no extra bookkeeping: a pass skips songs that
already carry what it would add. Stopping after 300 of 500 and rerunning picks
up at 301, because the first 300 now have a MusicBrainz identifier.

Failures never destroy existing data. A provider error on one song is logged,
recorded as unresolved where useful, and the run continues.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.resolver import (
    Candidate,
    ExistingSong,
    MatchConfidence,
    resolve_match,
)
from app.catalog.writer import (
    attach_genres,
    attach_identifier,
    attach_isrc,
    link_artist,
    recompute_eligibility,
    record_unresolved,
    store_provider_metadata,
    upsert_artist,
    upsert_song,
)
from app.core.errors import AudidleError
from app.database.catalog_models import CatalogSong, ExternalIdentifierRow
from app.models.song import (
    ID_TYPE_ISRC,
    ID_TYPE_RECORDING_MBID,
    ID_TYPE_TRACK,
    PROVIDER_ISRC,
    PROVIDER_MUSICBRAINZ,
    PROVIDER_SPOTIFY,
)
from app.providers.musicbrainz.musicbrainz_provider import MusicBrainzProvider
from app.providers.spotify.spotify_song_provider import SpotifySongProvider

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentReport:
    """What a pass did. Printed by the CLI."""

    considered: int = 0
    skipped_already_done: int = 0
    matched: int = 0
    unresolved: int = 0
    failed: int = 0
    isrcs_added: int = 0
    genres_added: int = 0
    identifiers_added: int = 0
    reasons: list[str] = field(default_factory=list)


async def _load_existing(session: AsyncSession, song: CatalogSong) -> ExistingSong:
    """Build the resolver's view of a song already in the catalog."""
    provider_ids: dict[str, str] = {}
    isrcs: list[str] = []
    mbid = None

    for identifier in song.identifiers:
        if identifier.identifier_type == ID_TYPE_ISRC:
            isrcs.append(identifier.identifier)
        elif identifier.identifier_type == ID_TYPE_RECORDING_MBID:
            mbid = identifier.identifier
        else:
            provider_ids[identifier.provider] = identifier.identifier

    return ExistingSong(
        song_id=song.id,
        title=song.title,
        artist=song.artist_credit,
        isrcs=tuple(isrcs),
        duration_ms=song.duration_ms,
        musicbrainz_id=mbid,
        provider_ids=provider_ids,
    )


async def _has_identifier(
    session: AsyncSession, song_id: str, provider: str, identifier_type: str
) -> bool:
    """Whether a song already carries an identifier of a given kind."""
    statement = select(ExternalIdentifierRow.id).where(
        ExternalIdentifierRow.song_id == song_id,
        ExternalIdentifierRow.provider == provider,
        ExternalIdentifierRow.identifier_type == identifier_type,
    )
    return (await session.execute(statement)).first() is not None


# -- MusicBrainz -------------------------------------------------------------


async def enrich_with_musicbrainz(
    session: AsyncSession,
    provider: MusicBrainzProvider,
    limit: int | None = None,
    progress=None,
    force: bool = False,
) -> EnrichmentReport:
    """Attach MusicBrainz identity, ISRCs, and tags to catalog songs.

    Resolution order matters and reflects what actually works:

    1. If the song already has an ISRC, look the recording up *by that ISRC*.
       This is exact, costs one request, and avoids the title-search trap
       entirely.
    2. Otherwise search by title and artist and run the candidate through the
       resolver, which refuses weak matches rather than taking the top score.

    Step one is the important one. Searching "Blinding Lights" by The Weeknd
    returns several recordings scored 100 with different ISRCs, so taking the
    top result would attach the wrong recording identity.
    """
    report = EnrichmentReport()

    songs = (await session.execute(select(CatalogSong))).scalars().all()
    if limit:
        songs = songs[:limit]

    for index, song in enumerate(songs, start=1):
        report.considered += 1

        # Resumability: already enriched, so skip without spending a request.
        # `force` exists because this check keys on the identifier, so it also
        # skips songs that are missing data a later version of the pass would
        # now collect. Backfilling that needs a re-run.
        if not force and await _has_identifier(
            session, song.id, PROVIDER_MUSICBRAINZ, ID_TYPE_RECORDING_MBID
        ):
            report.skipped_already_done += 1
            continue

        existing = await _load_existing(session, song)

        try:
            recording = None

            # 0. Already identified, so go straight to the known recording.
            if existing.musicbrainz_id:
                recording = await provider.get_recording(existing.musicbrainz_id)

            # 1. Exact lookup by an ISRC we already hold.
            if recording is None:
                for isrc in existing.isrcs:
                    recording = await provider.get_recording_by_isrc(isrc)
                    if recording is not None:
                        break

            if recording is not None:
                confidence = MatchConfidence.EXACT_ISRC
                reason = "matched by ISRC"
                # The ISRC endpoint returns artists and ISRCs but no tags, so
                # a second lookup is needed for genres. It is cached, and one
                # extra request per song is worth the genre data.
                full = await provider.get_recording(recording.mbid)
                if full is not None:
                    recording = full
            else:
                # 2. Fall back to search, judged by the resolver.
                candidates = await provider.search_recordings(
                    song.title, existing.artist, limit=5
                )
                recording, confidence, reason = _pick_recording(candidates, existing)

            if recording is None:
                report.unresolved += 1
                report.reasons.append(f"{song.title}: {reason}")
                await record_unresolved(
                    session,
                    PROVIDER_MUSICBRAINZ,
                    f"{song.title} - {song.artist_credit}: {reason}",
                    {"title": song.title, "artist": song.artist_credit},
                    song,
                )
                continue

            # -- Accepted. Write what MusicBrainz knows. --------------------
            if await attach_identifier(
                session,
                song,
                PROVIDER_MUSICBRAINZ,
                ID_TYPE_RECORDING_MBID,
                recording.mbid,
                confidence=confidence.value,
            ):
                report.identifiers_added += 1

            for isrc in recording.isrcs:
                if await attach_isrc(session, song, isrc):
                    report.isrcs_added += 1

            # Tags become genres. Missing tags add nothing rather than
            # inventing a genre.
            if recording.tags:
                report.genres_added += await attach_genres(
                    session, song, list(recording.tags), source=PROVIDER_MUSICBRAINZ
                )

            # MusicBrainz is the one source that gives us stable artist ids, so
            # this both creates the artist and links it in credit order.
            for position, credited in enumerate(recording.artists):
                artist = await upsert_artist(
                    session, credited.name, musicbrainz_artist_id=credited.mbid
                )
                await link_artist(
                    session, song, artist, position=position, credited_name=credited.name
                )

            # Fill gaps only. Never overwrite what we already had.
            await upsert_song(
                session,
                song_id=song.id,
                title=song.title,
                artist_credit=song.artist_credit,
                duration_ms=song.duration_ms or recording.length_ms,
                release_year=song.release_year or recording.first_release_year,
                explicit=song.explicit,
            )

            await store_provider_metadata(
                session,
                song,
                PROVIDER_MUSICBRAINZ,
                recording.mbid,
                {
                    "title": recording.title,
                    "artist_credit": recording.artist_credit,
                    "isrcs": list(recording.isrcs),
                    "tags": list(recording.tags),
                    "length_ms": recording.length_ms,
                },
            )

            await recompute_eligibility(session, song)
            report.matched += 1

        except AudidleError as error:
            # A provider failure must not destroy the existing record or stop
            # the run. The song simply stays un-enriched and is retried next time.
            report.failed += 1
            logger.warning("MusicBrainz enrichment failed for %s: %s", song.title, error)
        except asyncio.CancelledError:
            raise
        except Exception:
            report.failed += 1
            logger.exception("unexpected error enriching %s", song.title)

        # Commit as we go, so an interrupted run keeps what it finished.
        await session.commit()

        if progress:
            progress(index, len(songs), song.title)

    return report


def _pick_recording(candidates, existing: ExistingSong):
    """Choose a MusicBrainz recording for a song, or refuse.

    Runs every candidate through the resolver rather than trusting the search
    score, and requires a unique winner.
    """
    if not candidates:
        return None, MatchConfidence.UNRESOLVED, "no MusicBrainz results"

    accepted = []
    for recording in candidates:
        candidate = Candidate(
            provider=PROVIDER_MUSICBRAINZ,
            external_id=recording.mbid,
            title=recording.title,
            artist=recording.primary_artist,
            isrcs=recording.isrcs,
            duration_ms=recording.length_ms,
            musicbrainz_id=recording.mbid,
        )
        result = resolve_match(candidate, existing)
        if result.accepted:
            accepted.append((recording, result))

    if not accepted:
        return None, MatchConfidence.UNRESOLVED, "no candidate passed the confidence bar"

    if len(accepted) > 1:
        # Several plausible recordings. Picking one would be a guess, and the
        # wrong guess is silent, so this refuses.
        return (
            None,
            MatchConfidence.UNRESOLVED,
            f"{len(accepted)} MusicBrainz recordings matched, ambiguous",
        )

    recording, result = accepted[0]
    return recording, result.confidence, result.reason


# -- Spotify -----------------------------------------------------------------


async def enrich_with_spotify(
    session: AsyncSession,
    provider: SpotifySongProvider,
    limit: int | None = None,
    progress=None,
    force: bool = False,
) -> EnrichmentReport:
    """Attach Spotify identity and artwork, matched through ISRC.

    ISRC first, which is the whole point of holding them. Spotify supports
    `isrc:` as a search qualifier, so a song with an ISRC resolves to exactly
    the right track rather than to whichever release ranks highest.
    """
    report = EnrichmentReport()

    songs = (await session.execute(select(CatalogSong))).scalars().all()
    if limit:
        songs = songs[:limit]

    for index, song in enumerate(songs, start=1):
        report.considered += 1

        if not force and await _has_identifier(
            session, song.id, PROVIDER_SPOTIFY, ID_TYPE_TRACK
        ):
            report.skipped_already_done += 1
            continue

        existing = await _load_existing(session, song)

        try:
            match = None
            confidence = MatchConfidence.UNRESOLVED
            reason = "no ISRC and no confident metadata match"

            # 1. Exact, through ISRC.
            for isrc in existing.isrcs:
                results = await provider.search(f"isrc:{isrc}", limit=1)
                if results:
                    match = results[0]
                    confidence = MatchConfidence.EXACT_ISRC
                    reason = f"matched by ISRC {isrc}"
                    break

            # 2. Fall back to title and artist, judged by the resolver.
            if match is None:
                results = await provider.search(
                    f"track:{song.title} artist:{existing.artist}", limit=5
                )
                accepted = []
                for found in results:
                    candidate = Candidate(
                        provider=PROVIDER_SPOTIFY,
                        external_id=found.id,
                        title=found.title,
                        artist=found.artist,
                        isrcs=found.isrcs,
                        duration_ms=found.duration_ms,
                    )
                    result = resolve_match(candidate, existing)
                    if result.accepted:
                        accepted.append((found, result))

                if len(accepted) == 1:
                    match, result = accepted[0]
                    confidence, reason = result.confidence, result.reason
                elif len(accepted) > 1:
                    reason = f"{len(accepted)} Spotify tracks matched, ambiguous"

            if match is None:
                report.unresolved += 1
                report.reasons.append(f"{song.title}: {reason}")
                await record_unresolved(
                    session,
                    PROVIDER_SPOTIFY,
                    f"{song.title} - {song.artist_credit}: {reason}",
                    {"title": song.title, "artist": song.artist_credit},
                    song,
                )
                continue

            if await attach_identifier(
                session,
                song,
                PROVIDER_SPOTIFY,
                ID_TYPE_TRACK,
                match.id,
                confidence=confidence.value,
            ):
                report.identifiers_added += 1

            for isrc in match.isrcs:
                if await attach_isrc(session, song, isrc):
                    report.isrcs_added += 1

            # Artwork and links are what Spotify is genuinely best at, so fill
            # them in where we have nothing.
            await upsert_song(
                session,
                song_id=song.id,
                title=song.title,
                artist_credit=song.artist_credit,
                album=song.album or match.album,
                artwork_url=song.artwork_url or match.artwork_url,
                external_url=song.external_url or match.external_url,
                duration_ms=song.duration_ms or match.duration_ms,
                release_year=song.release_year or match.release_year,
                explicit=song.explicit or match.explicit,
            )

            await store_provider_metadata(
                session,
                song,
                PROVIDER_SPOTIFY,
                match.id,
                {
                    "title": match.title,
                    "artist": match.artist,
                    "album": match.album,
                    "artwork_url": match.artwork_url,
                },
            )

            await recompute_eligibility(session, song)
            report.matched += 1

        except AudidleError as error:
            report.failed += 1
            logger.warning("Spotify enrichment failed for %s: %s", song.title, error)
        except asyncio.CancelledError:
            raise
        except Exception:
            report.failed += 1
            logger.exception("unexpected error enriching %s", song.title)

        await session.commit()

        if progress:
            progress(index, len(songs), song.title)

    return report


async def recompute_all_eligibility(session: AsyncSession) -> dict[str, int]:
    """Recalculate eligibility for the whole catalog.

    Run after a difficulty threshold change, an audio import, or anything else
    that could move songs into or out of the game pool.
    """
    songs = (await session.execute(select(CatalogSong))).scalars().all()
    counts = {"total": len(songs), "eligible": 0}

    for song in songs:
        record = await recompute_eligibility(session, song)
        if record.eligible:
            counts["eligible"] += 1

    return counts


__all__ = [
    "EnrichmentReport",
    "enrich_with_musicbrainz",
    "enrich_with_spotify",
    "recompute_all_eligibility",
    "PROVIDER_ISRC",
]
