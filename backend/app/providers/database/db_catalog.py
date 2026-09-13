"""Providers backed by the curated song database.

Two providers over one table, because they answer genuinely different questions
and the rest of the application should keep seeing them as separate ports:

  - DbSongCatalogProvider  -> identity and metadata
  - DbPopularityProvider   -> difficulty tiers

Reading metadata from our own database rather than from Spotify is deliberate.
It was resolved once at ingest, so song selection and the reveal need no network
call at all. The game keeps working through a Spotify outage or a rate limit,
and rounds start faster.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import CuratedSong
from app.models.enums import Difficulty
from app.models.song import Song, SongPopularity, SongSelectionCriteria
from app.providers.base import PopularityProvider, SongCatalogProvider

logger = logging.getLogger(__name__)


def to_song(row: CuratedSong) -> Song:
    """Map a database row onto the domain model."""
    return Song(
        track_id=row.track_id,
        title=row.title,
        artist=row.artist,
        isrc=row.isrc,
        album=row.album,
        artwork_url=row.artwork_url,
        external_url=row.external_url,
        release_year=row.release_year,
        duration_ms=row.duration_ms,
        explicit=row.explicit,
        genres=tuple(genre for genre in row.genres.split(",") if genre),
    )


class DbSongCatalogProvider(SongCatalogProvider):
    """Catalog provider over the curated table.

    Its search covers only the curated songs, so it is suitable as the metadata
    source but usually not as the search source. Players should be able to guess
    anything, not just the songs that can be drawn, which is why this is
    normally paired with Spotify search. See CompositeSongCatalogProvider.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def search(self, query: str, limit: int = 10) -> list[Song]:
        normalized = f"%{query.strip().lower()}%"
        async with self._sessions() as session:
            statement = (
                select(CuratedSong)
                .where(
                    CuratedSong.title.ilike(normalized) | CuratedSong.artist.ilike(normalized)
                )
                .order_by(CuratedSong.stream_estimate.desc())
                .limit(limit)
            )
            rows = (await session.execute(statement)).scalars().all()
        return [to_song(row) for row in rows]

    async def get_song(self, track_id: str) -> Song | None:
        async with self._sessions() as session:
            row = await session.get(CuratedSong, track_id)
        return to_song(row) if row else None

    async def get_songs(self, track_ids: list[str]) -> list[Song]:
        if not track_ids:
            return []
        async with self._sessions() as session:
            statement = select(CuratedSong).where(CuratedSong.track_id.in_(track_ids))
            rows = (await session.execute(statement)).scalars().all()
        return [to_song(row) for row in rows]


class CompositeSongCatalogProvider(SongCatalogProvider):
    """Splits search and metadata across two providers.

    The combination the game actually wants: search the whole Spotify catalog so
    a player can guess any song, but resolve metadata locally so the hot path
    never depends on Spotify being up.

    Metadata lookups fall through to the search provider for ids the local
    catalog does not hold, which is exactly what happens when a player guesses a
    song that is not one of the curated ones.
    """

    def __init__(
        self, search_provider: SongCatalogProvider, metadata_provider: SongCatalogProvider
    ) -> None:
        self._search = search_provider
        self._metadata = metadata_provider

    async def search(self, query: str, limit: int = 10) -> list[Song]:
        return await self._search.search(query, limit=limit)

    async def get_song(self, track_id: str) -> Song | None:
        local = await self._metadata.get_song(track_id)
        if local is not None:
            return local
        # Not a curated song. Almost always a wrong guess, which still needs
        # resolving so it can be shown in the attempts list.
        return await self._search.get_song(track_id)

    async def get_songs(self, track_ids: list[str]) -> list[Song]:
        found = await self._metadata.get_songs(track_ids)
        known = {song.track_id for song in found}

        missing = [track_id for track_id in track_ids if track_id not in known]
        if missing:
            found.extend(await self._search.get_songs(missing))
        return found


class DbPopularityProvider(PopularityProvider):
    """Difficulty tiers from the curated table.

    This is the provider that exists because Spotify will not tell us how
    popular anything is. Tiers are precomputed at ingest, so selection is one
    indexed query rather than a scan plus classification.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        require_audio: bool = True,
    ) -> None:
        self._sessions = session_factory
        # When true, songs with no audio file are never offered for selection.
        # Without this the game would draw songs it cannot play and rely on
        # SongService's retry loop to dig its way out, which fails once most of
        # the catalog lacks audio.
        self._require_audio = require_audio

    async def get_popularity(self, track_id: str) -> SongPopularity | None:
        async with self._sessions() as session:
            row = await session.get(CuratedSong, track_id)
        if row is None:
            return None
        return SongPopularity(
            track_id=row.track_id,
            difficulty=Difficulty(row.difficulty),
            stream_estimate=row.stream_estimate,
        )

    async def get_eligible_track_ids(self, criteria: SongSelectionCriteria) -> list[str]:
        async with self._sessions() as session:
            statement = select(CuratedSong.track_id).where(
                CuratedSong.difficulty == criteria.difficulty.value
            )
            if self._require_audio:
                statement = statement.where(CuratedSong.audio_file.is_not(None))
            if criteria.exclude_track_ids:
                statement = statement.where(
                    CuratedSong.track_id.not_in(criteria.exclude_track_ids)
                )
            # Metadata filters such as genre and decade are applied by
            # SongService, which holds the Song objects to test them against.
            rows = (await session.execute(statement)).scalars().all()
        return list(rows)

    async def get_difficulty(self, track_id: str) -> Difficulty | None:
        async with self._sessions() as session:
            row = await session.get(CuratedSong, track_id)
        return Difficulty(row.difficulty) if row else None
