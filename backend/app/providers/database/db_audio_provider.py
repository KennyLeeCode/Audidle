"""AudioProvider over audio files attached to curated songs.

Reads the audio_file column written by scripts/attach_audio.py and serves the
file from the audio directory. Returning None for a song with no attached audio
is the normal case, not an error: it is how a curated row that has identity and
difficulty but no playable source is kept out of rounds.
"""

import logging
import mimetypes
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import CuratedSong
from app.models.enums import PlayableSourceKind
from app.models.song import PlayableSource
from app.providers.base import AudioProvider

logger = logging.getLogger(__name__)


class DbAudioProvider(AudioProvider):
    """Local file audio, indexed by the curated song table."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], audio_dir: Path
    ) -> None:
        self._sessions = session_factory
        self._audio_dir = audio_dir

    async def _resolve_path(self, track_id: str) -> tuple[Path, int | None] | None:
        async with self._sessions() as session:
            row = await session.get(CuratedSong, track_id)

        if row is None or not row.audio_file:
            return None

        path = self._audio_dir / row.audio_file
        # Guard against a path escaping the audio directory via a crafted
        # filename. The column is written by a local script rather than by user
        # input, but serving arbitrary files is not a mistake worth risking.
        try:
            path.resolve().relative_to(self._audio_dir.resolve())
        except ValueError:
            logger.error("audio path for %s escapes the audio directory", track_id)
            return None

        if not path.is_file():
            # The row claims audio that is not on disk. Treated as unplayable so
            # selection moves on rather than failing the round.
            logger.warning("audio file missing for %s: %s", track_id, row.audio_file)
            return None

        return path, row.duration_ms

    async def get_playable_source(self, track_id: str) -> PlayableSource | None:
        resolved = await self._resolve_path(track_id)
        if resolved is None:
            return None

        _, duration_ms = resolved
        return PlayableSource(
            track_id=track_id,
            kind=PlayableSourceKind.FILE_URL,
            # Filled in with the round scoped proxy URL by the API layer, so the
            # filename never reaches the browser.
            url=None,
            duration_ms=duration_ms,
            # Local files are fully decodable, so Web Audio can hit the 0.01s
            # stage exactly.
            supports_precise_clips=True,
        )

    async def open_stream(self, track_id: str) -> tuple[bytes, str] | None:
        resolved = await self._resolve_path(track_id)
        if resolved is None:
            return None

        path, _ = resolved
        media_type, _ = mimetypes.guess_type(path.name)
        return path.read_bytes(), media_type or "application/octet-stream"
