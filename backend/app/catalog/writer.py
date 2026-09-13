"""Idempotent writes into the Audidle catalog.

Everything that creates or updates catalog rows goes through here, so the
idempotency rules live in one place rather than being re-implemented by each
import script. Running any ingestion twice must not produce a second song, a
second artist, a duplicate identifier, a duplicate genre link, a second
popularity row, or a duplicate audio asset.

The mechanism is the same each time: look up by the natural key first, insert
only if absent, update in place otherwise.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.genres import display_name, slugify_genre
from app.config.difficulties import difficulty_for_streams
from app.database.catalog_models import (
    Artist,
    AudioAsset,
    CatalogSong,
    ExternalIdentifierRow,
    Genre,
    SongArtist,
    SongEligibility,
    SongGenre,
    SongPopularityRow,
    SongProviderMetadata,
    UnresolvedMatch,
)
from app.models.song import ID_TYPE_ISRC, PROVIDER_ISRC
from app.services.guess_matcher import normalize_artist

logger = logging.getLogger(__name__)


def new_song_id() -> str:
    """Audidle's own recording id."""
    return str(uuid.uuid4())


async def find_song_by_identifier(
    session: AsyncSession, provider: str, identifier: str
) -> CatalogSong | None:
    """Look up a song by any external identifier.

    The core of deduplication. Before creating a song, ingestion asks whether
    any of its identifiers already points at one.
    """
    statement = (
        select(CatalogSong)
        .join(ExternalIdentifierRow)
        .where(
            ExternalIdentifierRow.provider == provider,
            ExternalIdentifierRow.identifier == identifier,
        )
    )
    return (await session.execute(statement)).scalars().first()


async def find_song_by_isrc(session: AsyncSession, isrc: str) -> CatalogSong | None:
    """Look up a song by ISRC, the preferred cross-provider key."""
    return await find_song_by_identifier(session, PROVIDER_ISRC, isrc)


async def upsert_song(
    session: AsyncSession,
    *,
    song_id: str | None,
    title: str,
    artist_credit: str,
    album: str | None = None,
    artwork_url: str | None = None,
    external_url: str | None = None,
    duration_ms: int | None = None,
    release_year: int | None = None,
    explicit: bool = False,
) -> CatalogSong:
    """Create or update a song row.

    Updates only overwrite a field when the new value is not None, so an
    enrichment pass that knows less than a previous one cannot erase data.
    """
    song = await session.get(CatalogSong, song_id) if song_id else None

    if song is None:
        song = CatalogSong(
            id=song_id or new_song_id(),
            title=title,
            artist_credit=artist_credit,
        )
        session.add(song)

    song.title = title or song.title
    song.artist_credit = artist_credit or song.artist_credit
    if album is not None:
        song.album = album
    if artwork_url is not None:
        song.artwork_url = artwork_url
    if external_url is not None:
        song.external_url = external_url
    if duration_ms is not None:
        song.duration_ms = duration_ms
    if release_year is not None:
        song.release_year = release_year
    song.explicit = explicit

    await session.flush()
    return song


async def attach_identifier(
    session: AsyncSession,
    song: CatalogSong,
    provider: str,
    identifier_type: str,
    identifier: str,
    confidence: str = "exact_provider_id",
) -> ExternalIdentifierRow | None:
    """Attach an external id, or return the existing row.

    Returns None and logs when the identifier is already attached to a
    *different* song. That is a real conflict rather than a duplicate: two songs
    both claiming one Spotify track id means an earlier match was wrong, and
    silently repointing it would corrupt whichever one is correct.
    """
    existing = (
        await session.execute(
            select(ExternalIdentifierRow).where(
                ExternalIdentifierRow.provider == provider,
                ExternalIdentifierRow.identifier_type == identifier_type,
                ExternalIdentifierRow.identifier == identifier,
            )
        )
    ).scalars().first()

    if existing is not None:
        if existing.song_id != song.id:
            logger.warning(
                "identifier %s:%s already belongs to song %s, not attaching to %s",
                provider,
                identifier,
                existing.song_id,
                song.id,
            )
            return None
        return existing

    row = ExternalIdentifierRow(
        song_id=song.id,
        provider=provider,
        identifier_type=identifier_type,
        identifier=identifier,
        confidence=confidence,
    )
    session.add(row)
    await session.flush()
    return row


async def attach_isrc(
    session: AsyncSession, song: CatalogSong, isrc: str, confidence: str = "exact_isrc"
) -> ExternalIdentifierRow | None:
    """Attach an ISRC. A song may have several, so this never replaces."""
    return await attach_identifier(
        session, song, PROVIDER_ISRC, ID_TYPE_ISRC, isrc.strip().upper(), confidence
    )


async def upsert_artist(
    session: AsyncSession, name: str, musicbrainz_artist_id: str | None = None
) -> Artist:
    """Find or create an artist.

    Matched on the MusicBrainz id when there is one, otherwise on the
    normalized name, which is what keeps "Beyoncé" and "Beyonce" as one artist.
    """
    if musicbrainz_artist_id:
        found = (
            await session.execute(
                select(Artist).where(Artist.musicbrainz_artist_id == musicbrainz_artist_id)
            )
        ).scalars().first()
        if found is not None:
            return found

    # Matched on the normalized column rather than a LIKE over the display
    # name, because that comparison is accent sensitive and would treat
    # "Beyonce" as a different artist from "Beyoncé".
    normalized = normalize_artist(name)
    found = (
        await session.execute(select(Artist).where(Artist.normalized_name == normalized))
    ).scalars().first()
    if found is not None:
        if musicbrainz_artist_id and not found.musicbrainz_artist_id:
            found.musicbrainz_artist_id = musicbrainz_artist_id
        return found

    artist = Artist(
        id=str(uuid.uuid4()),
        name=name,
        normalized_name=normalized,
        musicbrainz_artist_id=musicbrainz_artist_id,
    )
    session.add(artist)
    await session.flush()
    return artist


async def link_artist(
    session: AsyncSession,
    song: CatalogSong,
    artist: Artist,
    position: int = 0,
    credited_name: str | None = None,
) -> None:
    """Link a song to an artist at a credit position, idempotently."""
    existing = await session.get(SongArtist, (song.id, artist.id))
    if existing is not None:
        existing.position = position
        return

    session.add(
        SongArtist(
            song_id=song.id,
            artist_id=artist.id,
            position=position,
            credited_name=credited_name,
        )
    )
    await session.flush()


async def upsert_genre(session: AsyncSession, name: str) -> Genre | None:
    """Find or create a genre by its canonical slug."""
    slug = slugify_genre(name)
    if not slug:
        return None

    found = (
        await session.execute(select(Genre).where(Genre.slug == slug))
    ).scalars().first()
    if found is not None:
        return found

    genre = Genre(name=display_name(slug), slug=slug)
    session.add(genre)
    await session.flush()
    return genre


async def attach_genres(
    session: AsyncSession,
    song: CatalogSong,
    names: list[str],
    source: str,
    confidence: float = 1.0,
) -> int:
    """Attach genres to a song. Never invents one for missing data."""
    attached = 0
    for name in names:
        if not name or not name.strip():
            continue
        genre = await upsert_genre(session, name)
        if genre is None:
            continue

        existing = await session.get(SongGenre, (song.id, genre.id))
        if existing is not None:
            continue

        session.add(
            SongGenre(
                song_id=song.id, genre_id=genre.id, source=source, confidence=confidence
            )
        )
        attached += 1

    await session.flush()
    return attached


async def upsert_popularity(
    session: AsyncSession,
    song: CatalogSong,
    provider: str,
    stream_count: int | None = None,
    popularity_score: int | None = None,
    confidence: str = "medium",
    measured_at: str | None = None,
    source_reference: str | None = None,
) -> SongPopularityRow:
    """Record a popularity measurement, one row per provider per song."""
    existing = (
        await session.execute(
            select(SongPopularityRow).where(
                SongPopularityRow.song_id == song.id,
                SongPopularityRow.provider == provider,
            )
        )
    ).scalars().first()

    row = existing or SongPopularityRow(song_id=song.id, provider=provider)
    row.stream_count = stream_count
    row.popularity_score = popularity_score
    row.confidence = confidence
    row.measured_at = measured_at
    row.source_reference = source_reference

    if existing is None:
        session.add(row)
    await session.flush()
    return row


async def upsert_audio_asset(
    session: AsyncSession,
    song: CatalogSong,
    provider: str,
    provider_reference: str,
    duration_ms: int | None = None,
    playable: bool = False,
) -> AudioAsset:
    """Attach an audio asset.

    `playable` defaults to False. It is set by validation against the actual
    file, never assumed, so a row pointing at missing audio cannot keep a song
    in the game pool.
    """
    existing = (
        await session.execute(
            select(AudioAsset).where(
                AudioAsset.song_id == song.id,
                AudioAsset.provider == provider,
                AudioAsset.provider_reference == provider_reference,
            )
        )
    ).scalars().first()

    row = existing or AudioAsset(
        song_id=song.id, provider=provider, provider_reference=provider_reference
    )
    row.duration_ms = duration_ms if duration_ms is not None else row.duration_ms
    row.playable = playable
    if playable:
        row.validated_at = datetime.now(UTC)

    if existing is None:
        session.add(row)
    await session.flush()
    return row


async def store_provider_metadata(
    session: AsyncSession,
    song: CatalogSong,
    provider: str,
    external_id: str | None,
    payload: dict,
) -> None:
    """Keep a provider's raw payload without letting it into the songs table."""
    existing = (
        await session.execute(
            select(SongProviderMetadata).where(
                SongProviderMetadata.song_id == song.id,
                SongProviderMetadata.provider == provider,
            )
        )
    ).scalars().first()

    row = existing or SongProviderMetadata(song_id=song.id, provider=provider)
    row.external_id = external_id
    row.metadata_json = json.dumps(payload, separators=(",", ":"))
    row.last_synced_at = datetime.now(UTC)

    if existing is None:
        session.add(row)
    await session.flush()


async def record_unresolved(
    session: AsyncSession,
    provider: str,
    reason: str,
    candidate_payload: dict,
    song: CatalogSong | None = None,
    confidence: str = "unresolved",
) -> None:
    """Park a match that was not confident enough to accept.

    Idempotent on (song, provider, reason) so a resumed run does not pile up
    duplicate review items.
    """
    statement = select(UnresolvedMatch).where(
        UnresolvedMatch.provider == provider,
        UnresolvedMatch.reason == reason,
        UnresolvedMatch.status == "pending",
    )
    statement = statement.where(
        UnresolvedMatch.song_id == (song.id if song else None)
    )
    if (await session.execute(statement)).scalars().first() is not None:
        return

    session.add(
        UnresolvedMatch(
            song_id=song.id if song else None,
            provider=provider,
            reason=reason,
            candidate_payload=json.dumps(candidate_payload, separators=(",", ":")),
            confidence=confidence,
        )
    )
    await session.flush()


async def recompute_eligibility(session: AsyncSession, song: CatalogSong) -> SongEligibility:
    """Recalculate whether a song can be used in a round.

    The explicit gate between "we know about this song" and "this song can be
    drawn". Each requirement is stored separately so the validator can report
    which one a song is failing rather than just that it is ineligible.
    """
    identifiers = (
        await session.execute(
            select(ExternalIdentifierRow).where(ExternalIdentifierRow.song_id == song.id)
        )
    ).scalars().all()

    popularity = (
        await session.execute(
            select(SongPopularityRow).where(SongPopularityRow.song_id == song.id)
        )
    ).scalars().all()

    assets = (
        await session.execute(select(AudioAsset).where(AudioAsset.song_id == song.id))
    ).scalars().all()

    difficulty = None
    for row in popularity:
        if row.stream_count is not None:
            difficulty = difficulty_for_streams(row.stream_count)
            break

    has_identity = bool(identifiers)
    has_difficulty = difficulty is not None
    has_audio = bool(assets)
    audio_playable = any(asset.playable for asset in assets)
    metadata_complete = bool(song.title and song.artist_credit)

    record = await session.get(SongEligibility, song.id)
    if record is None:
        record = SongEligibility(song_id=song.id)
        session.add(record)

    record.has_identity = has_identity
    record.has_difficulty = has_difficulty
    record.has_audio = has_audio
    record.audio_playable = audio_playable
    record.metadata_complete = metadata_complete
    record.difficulty = difficulty
    record.eligible = (
        has_identity and has_difficulty and audio_playable and metadata_complete
    )
    record.computed_at = datetime.now(UTC)

    await session.flush()
    return record
