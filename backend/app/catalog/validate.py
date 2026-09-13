"""Catalog statistics and validation.

Two different questions. `catalog_stats` answers "what do we have", and
`validate_catalog` answers "what is wrong with it". Keeping them apart means
the healthy-case report stays readable instead of being buried in warnings.
"""

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.catalog_models import (
    Artist,
    AudioAsset,
    CatalogSong,
    ExternalIdentifierRow,
    Genre,
    SongEligibility,
    SongGenre,
    SongPopularityRow,
    UnresolvedMatch,
)
from app.models.enums import Difficulty
from app.models.song import (
    ID_TYPE_ISRC,
    ID_TYPE_RECORDING_MBID,
    ID_TYPE_TRACK,
    PROVIDER_ISRC,
    PROVIDER_MUSICBRAINZ,
    PROVIDER_SPOTIFY,
)


@dataclass
class CatalogStats:
    total_songs: int = 0
    total_artists: int = 0
    total_genres: int = 0
    with_isrc: int = 0
    with_musicbrainz: int = 0
    with_spotify: int = 0
    with_popularity: int = 0
    with_genres: int = 0
    with_audio: int = 0
    playable: int = 0
    eligible: int = 0
    unresolved: int = 0
    by_difficulty: dict[str, int] = field(default_factory=dict)


@dataclass
class CatalogProblems:
    missing_identity: list[str] = field(default_factory=list)
    missing_difficulty: list[str] = field(default_factory=list)
    missing_audio: list[str] = field(default_factory=list)
    unplayable_audio: list[str] = field(default_factory=list)
    missing_metadata: list[str] = field(default_factory=list)
    missing_genres: list[str] = field(default_factory=list)
    duplicate_isrcs: list[tuple[str, int]] = field(default_factory=list)
    possible_duplicates: list[tuple[str, str]] = field(default_factory=list)
    broken_audio_files: list[str] = field(default_factory=list)


async def _count_songs_with(session: AsyncSession, provider: str, id_type: str) -> int:
    statement = select(func.count(func.distinct(ExternalIdentifierRow.song_id))).where(
        ExternalIdentifierRow.provider == provider,
        ExternalIdentifierRow.identifier_type == id_type,
    )
    return (await session.execute(statement)).scalar_one()


async def catalog_stats(session: AsyncSession) -> CatalogStats:
    """Count what the catalog holds."""
    stats = CatalogStats()

    stats.total_songs = (await session.execute(select(func.count(CatalogSong.id)))).scalar_one()
    stats.total_artists = (await session.execute(select(func.count(Artist.id)))).scalar_one()
    stats.total_genres = (await session.execute(select(func.count(Genre.id)))).scalar_one()

    stats.with_isrc = await _count_songs_with(session, PROVIDER_ISRC, ID_TYPE_ISRC)
    stats.with_musicbrainz = await _count_songs_with(
        session, PROVIDER_MUSICBRAINZ, ID_TYPE_RECORDING_MBID
    )
    stats.with_spotify = await _count_songs_with(session, PROVIDER_SPOTIFY, ID_TYPE_TRACK)

    stats.with_popularity = (
        await session.execute(
            select(func.count(func.distinct(SongPopularityRow.song_id)))
        )
    ).scalar_one()
    stats.with_genres = (
        await session.execute(select(func.count(func.distinct(SongGenre.song_id))))
    ).scalar_one()
    stats.with_audio = (
        await session.execute(select(func.count(func.distinct(AudioAsset.song_id))))
    ).scalar_one()
    stats.playable = (
        await session.execute(
            select(func.count(func.distinct(AudioAsset.song_id))).where(
                AudioAsset.playable.is_(True)
            )
        )
    ).scalar_one()
    stats.eligible = (
        await session.execute(
            select(func.count(SongEligibility.song_id)).where(
                SongEligibility.eligible.is_(True)
            )
        )
    ).scalar_one()
    stats.unresolved = (
        await session.execute(
            select(func.count(UnresolvedMatch.id)).where(UnresolvedMatch.status == "pending")
        )
    ).scalar_one()

    rows = (
        await session.execute(
            select(SongEligibility.difficulty, func.count())
            .where(SongEligibility.eligible.is_(True))
            .group_by(SongEligibility.difficulty)
        )
    ).all()
    stats.by_difficulty = {str(difficulty): count for difficulty, count in rows if difficulty}

    return stats


async def validate_catalog(session: AsyncSession, audio_dir: Path) -> CatalogProblems:
    """Find what is missing or inconsistent."""
    problems = CatalogProblems()

    songs = (await session.execute(select(CatalogSong))).scalars().all()

    for song in songs:
        label = f"{song.title} - {song.artist_credit}"
        record = await session.get(SongEligibility, song.id)

        if record is None or not record.has_identity:
            problems.missing_identity.append(label)
        if record is None or not record.has_difficulty:
            problems.missing_difficulty.append(label)
        if record is None or not record.has_audio:
            problems.missing_audio.append(label)
        elif not record.audio_playable:
            problems.unplayable_audio.append(label)
        if not song.title or not song.artist_credit:
            problems.missing_metadata.append(label)
        if not song.genres:
            problems.missing_genres.append(label)

    # An ISRC pointing at more than one song means an earlier match was wrong.
    duplicate_rows = (
        await session.execute(
            select(ExternalIdentifierRow.identifier, func.count())
            .where(ExternalIdentifierRow.identifier_type == ID_TYPE_ISRC)
            .group_by(ExternalIdentifierRow.identifier)
            .having(func.count() > 1)
        )
    ).all()
    problems.duplicate_isrcs = [(identifier, count) for identifier, count in duplicate_rows]

    # Songs that look like the same recording under two catalog entries. Not
    # merged automatically, only reported, because a wrong merge is far harder
    # to undo than a duplicate is to review.
    from app.services.guess_matcher import normalize_artist, normalize_title

    seen: dict[tuple[str, str], str] = {}
    for song in songs:
        key = (normalize_title(song.title), normalize_artist(song.artist_credit))
        if not key[0]:
            continue
        if key in seen and seen[key] != song.id:
            problems.possible_duplicates.append((seen[key], song.id))
        else:
            seen[key] = song.id

    # Assets marked playable whose file is no longer on disk.
    assets = (
        await session.execute(select(AudioAsset).where(AudioAsset.playable.is_(True)))
    ).scalars().all()
    for asset in assets:
        if not (audio_dir / asset.provider_reference).is_file():
            problems.broken_audio_files.append(asset.provider_reference)

    return problems


def render_stats(stats: CatalogStats) -> str:
    """Format the stats the way the CLI prints them."""
    lines = [
        "",
        "AUDIDLE CATALOG",
        "",
        f"  Total recordings      {stats.total_songs:>7,}",
        f"  Artists               {stats.total_artists:>7,}",
        f"  Genres                {stats.total_genres:>7,}",
        "",
        f"  With ISRC             {stats.with_isrc:>7,}",
        f"  MusicBrainz matched   {stats.with_musicbrainz:>7,}",
        f"  Spotify matched       {stats.with_spotify:>7,}",
        f"  With popularity       {stats.with_popularity:>7,}",
        f"  With genre            {stats.with_genres:>7,}",
        f"  With audio            {stats.with_audio:>7,}",
        f"  Playable              {stats.playable:>7,}",
        f"  Game eligible         {stats.eligible:>7,}",
    ]

    if stats.unresolved:
        lines.append(f"  Unresolved matches    {stats.unresolved:>7,}")

    lines.append("")
    lines.append("  Eligible by difficulty:")
    for difficulty in Difficulty:
        count = stats.by_difficulty.get(difficulty.value, 0)
        flag = "   <- no rounds possible" if count == 0 else ""
        lines.append(f"    {difficulty.value:<12}{count:>7,}{flag}")

    return "\n".join(lines) + "\n"


def _section(title: str, items: list, limit: int = 8) -> list[str]:
    if not items:
        return []
    lines = [f"  {title}: {len(items)}"]
    for item in items[:limit]:
        lines.append(f"      {item}")
    if len(items) > limit:
        lines.append(f"      ... and {len(items) - limit} more")
    return lines


def render_problems(problems: CatalogProblems) -> str:
    lines = ["", "CATALOG VALIDATION", ""]

    sections = [
        ("Missing identity", problems.missing_identity),
        ("Missing difficulty", problems.missing_difficulty),
        ("Missing audio", problems.missing_audio),
        ("Audio not validated", problems.unplayable_audio),
        ("Missing metadata", problems.missing_metadata),
        ("Missing genres", problems.missing_genres),
        ("Broken audio files", problems.broken_audio_files),
    ]

    body: list[str] = []
    for title, items in sections:
        body.extend(_section(title, items))

    if problems.duplicate_isrcs:
        body.append(f"  Duplicate ISRCs: {len(problems.duplicate_isrcs)}")
        for identifier, count in problems.duplicate_isrcs[:8]:
            body.append(f"      {identifier} on {count} songs")

    if problems.possible_duplicates:
        body.append(f"  Possible duplicate recordings: {len(problems.possible_duplicates)}")
        for left, right in problems.possible_duplicates[:8]:
            body.append(f"      {left} / {right}")

    if not body:
        body = ["  nothing to report"]

    return "\n".join(lines + body) + "\n"
