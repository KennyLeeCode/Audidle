"""Local file AudioProvider over the generated mock audio.

This is the provider that makes the 0.01s stage genuinely work. It serves plain
WAV files, which the browser can fully decode, which in turn lets the frontend
use Web Audio to schedule a clip to the exact sample. A streaming or DRM backed
source cannot offer that, and declares so via supports_precise_clips.

Note the URL it hands out is round scoped and opaque. The filename is never
exposed, because "mock012.wav" appearing in devtools while a round is still
playing would be just as much of an answer leak as putting the title in JSON.
"""

import mimetypes
from pathlib import Path

from app.models.enums import PlayableSourceKind
from app.models.song import PlayableSource
from app.providers.base import AudioProvider
from app.providers.mock.catalog import load_mock_catalog


class MockAudioProvider(AudioProvider):
    """Audio provider backed by WAV files in app/data/audio."""

    def __init__(self, catalog_path: Path, audio_dir: Path) -> None:
        self._catalog = load_mock_catalog(catalog_path)
        self._audio_dir = audio_dir

    def _file_path(self, track_id: str) -> Path | None:
        entry = self._catalog.get(track_id)
        if entry is None:
            return None
        path = self._audio_dir / entry.audio_file
        # Missing file is a normal outcome, not a crash. SongService treats it
        # as "this candidate is unplayable" and draws a different song.
        return path if path.is_file() else None

    async def get_playable_source(self, track_id: str) -> PlayableSource | None:
        path = self._file_path(track_id)
        if path is None:
            return None

        entry = self._catalog.get(track_id)
        return PlayableSource(
            track_id=track_id,
            kind=PlayableSourceKind.FILE_URL,
            # Filled in with the round scoped proxy URL by SongService. Left
            # None here so the provider never assumes an HTTP routing shape.
            url=None,
            duration_ms=entry.song.duration_ms if entry else None,
            supports_precise_clips=True,
        )

    async def open_stream(self, track_id: str) -> tuple[bytes, str] | None:
        path = self._file_path(track_id)
        if path is None:
            return None
        mime_type, _ = mimetypes.guess_type(path.name)
        return path.read_bytes(), mime_type or "application/octet-stream"
