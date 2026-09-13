"""Providers over the Audidle catalog.

These are what the game actually reads. All three go to the local database and
nothing else, so opening a round never touches MusicBrainz or Spotify. External
APIs are for ingestion and enrichment, not for the selection path.

  AudidleSongCatalogProvider   identity and metadata
  AudidlePopularityProvider    the eligible pool for a difficulty
  AudidleAudioProvider         playable audio assets
"""

import logging
import mimetypes
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.catalog_models import (
    AudioAsset,
    CatalogSong,
    ExternalIdentifierRow,
    SongEligibility,
)
from app.models.enums import Difficulty, PlayableSourceKind
from app.models.song import (
    PROVIDER_AUDIDLE,
    ExternalIdentifier,
    PlayableSource,
    Song,
    SongPopularity,
    SongSelectionCriteria,
)
from app.providers.base import AudioProvider, PopularityProvider, SongCatalogProvider

logger = logging.getLogger(__name__)


def to_song(row: CatalogSong) -> Song:
    """Map a catalog row onto the domain model."""
    return Song(
        id=row.id,
        title=row.title,
        artist=row.artist_credit,
        album=row.album,
        artwork_url=row.artwork_url,
        external_url=row.external_url,
        release_year=row.release_year,
        duration_ms=row.duration_ms,
        explicit=row.explicit,
        genres=tuple(link.genre.slug for link in row.genres),
        external_ids=tuple(
            ExternalIdentifier(item.provider, item.identifier_type, item.identifier)
            for item in row.identifiers
        ),
    )


class AudidleSongCatalogProvider(SongCatalogProvider):
    """Catalog provider over the Audidle database.

    Its search covers only our own songs, so on its own it limits guessing to
    the catalog. Pair it with Spotify search through CompositeSongCatalogProvider
    to let players guess anything.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    @property
    def provider_name(self) -> str:
        return PROVIDER_AUDIDLE

    async def search(self, query: str, limit: int = 10) -> list[Song]:
        pattern = f"%{query.strip().lower()}%"
        async with self._sessions() as session:
            statement = (
                select(CatalogSong)
                .where(
                    CatalogSong.title.ilike(pattern)
                    | CatalogSong.artist_credit.ilike(pattern)
                )
                .limit(limit)
            )
            rows = (await session.execute(statement)).scalars().all()
        return [to_song(row) for row in rows]

    async def get_song(self, song_id: str) -> Song | None:
        async with self._sessions() as session:
            row = await session.get(CatalogSong, song_id)
            return to_song(row) if row else None

    async def get_songs(self, song_ids: list[str]) -> list[Song]:
        if not song_ids:
            return []
        async with self._sessions() as session:
            statement = select(CatalogSong).where(CatalogSong.id.in_(song_ids))
            rows = (await session.execute(statement)).scalars().all()
            return [to_song(row) for row in rows]

    async def resolve_external(self, provider: str, external_id: str) -> Song | None:
        """Resolve a provider's own id to an Audidle song.

        The server side half of the guess flow, and the reason search results
        can stay provider scoped. A player submits a Spotify track id and this
        finds the Audidle song carrying it, without the client ever holding an
        internal id.
        """
        if provider == PROVIDER_AUDIDLE:
            return await self.get_song(external_id)

        async with self._sessions() as session:
            statement = (
                select(CatalogSong)
                .join(ExternalIdentifierRow)
                .where(
                    ExternalIdentifierRow.provider == provider,
                    ExternalIdentifierRow.identifier == external_id,
                )
            )
            row = (await session.execute(statement)).scalars().first()
            return to_song(row) if row else None

    async def find_by_isrc(self, isrc: str) -> Song | None:
        """Look up a song by ISRC, the preferred cross-provider key."""
        from app.models.song import PROVIDER_ISRC

        return await self.resolve_external(PROVIDER_ISRC, isrc.strip().upper())


class AudidlePopularityProvider(PopularityProvider):
    """The eligible game pool, read from song_eligibility.

    Selection is one indexed query on (eligible, difficulty). Eligibility is
    computed by the pipeline rather than derived here, which is what keeps
    starting a round off the join path entirely.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_popularity(self, song_id: str) -> SongPopularity | None:
        async with self._sessions() as session:
            record = await session.get(SongEligibility, song_id)
        if record is None or record.difficulty is None:
            return None
        return SongPopularity(song_id=song_id, difficulty=Difficulty(record.difficulty))

    async def get_eligible_song_ids(self, criteria: SongSelectionCriteria) -> list[str]:
        async with self._sessions() as session:
            statement = select(SongEligibility.song_id).where(
                # Both conditions matter. A song can have a difficulty and still
                # be ineligible because its audio is missing or unvalidated.
                SongEligibility.eligible.is_(True),
                SongEligibility.difficulty == criteria.difficulty.value,
            )
            if criteria.exclude_song_ids:
                statement = statement.where(
                    SongEligibility.song_id.not_in(criteria.exclude_song_ids)
                )
            rows = (await session.execute(statement)).scalars().all()
        return list(rows)

    async def get_difficulty(self, song_id: str) -> Difficulty | None:
        async with self._sessions() as session:
            record = await session.get(SongEligibility, song_id)
        if record is None or record.difficulty is None:
            return None
        return Difficulty(record.difficulty)


class AudidleAudioProvider(AudioProvider):
    """Audio from the assets attached to catalog songs."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], audio_dir: Path
    ) -> None:
        self._sessions = session_factory
        self._audio_dir = audio_dir

    async def _resolve(self, song_id: str) -> tuple[Path, int | None] | None:
        async with self._sessions() as session:
            statement = select(AudioAsset).where(
                AudioAsset.song_id == song_id, AudioAsset.playable.is_(True)
            )
            asset = (await session.execute(statement)).scalars().first()

        if asset is None:
            return None

        path = self._audio_dir / asset.provider_reference
        # A crafted reference must not turn the audio route into a file reader.
        try:
            path.resolve().relative_to(self._audio_dir.resolve())
        except ValueError:
            logger.error("audio path for %s escapes the audio directory", song_id)
            return None

        if not path.is_file():
            # The asset claims audio that is not on disk. Unplayable rather than
            # an error, so selection moves on.
            logger.warning("audio file missing for %s: %s", song_id, asset.provider_reference)
            return None

        return path, asset.duration_ms

    async def get_playable_source(self, song_id: str) -> PlayableSource | None:
        resolved = await self._resolve(song_id)
        if resolved is None:
            return None

        _, duration_ms = resolved
        return PlayableSource(
            song_id=song_id,
            kind=PlayableSourceKind.FILE_URL,
            url=None,
            duration_ms=duration_ms,
            # Local files decode, so Web Audio can hit the 0.01s stage exactly.
            supports_precise_clips=True,
        )

    async def open_stream(self, song_id: str) -> tuple[bytes, str] | None:
        resolved = await self._resolve(song_id)
        if resolved is None:
            return None

        path, _ = resolved
        media_type, _ = mimetypes.guess_type(path.name)
        return path.read_bytes(), media_type or "application/octet-stream"
