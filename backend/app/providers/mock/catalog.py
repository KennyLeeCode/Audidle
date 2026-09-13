"""Shared in memory catalog backing every mock provider.

The three mock providers answer different questions about the same songs, so
they read from one loaded catalog rather than each parsing the JSON file. The
catalog is loaded once and cached.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config.difficulties import difficulty_for_streams
from app.models.enums import Difficulty
from app.models.song import (
    ID_TYPE_ISRC,
    ID_TYPE_TRACK,
    PROVIDER_ISRC,
    PROVIDER_MOCK,
    ExternalIdentifier,
    Song,
    SongPopularity,
)


@dataclass(frozen=True)
class MockCatalogEntry:
    """One song plus the mock-only extras the real providers get elsewhere."""

    song: Song
    popularity: SongPopularity
    audio_file: str


@dataclass(frozen=True)
class MockCatalog:
    """Loaded mock dataset, indexed for the lookups the providers need."""

    entries: dict[str, MockCatalogEntry]
    by_difficulty: dict[Difficulty, list[str]]

    def get(self, song_id: str) -> MockCatalogEntry | None:
        return self.entries.get(song_id)

    @property
    def songs(self) -> list[Song]:
        return [entry.song for entry in self.entries.values()]


def _build_entry(raw: dict) -> MockCatalogEntry:
    streams = int(raw["stream_estimate"])
    identifiers = [ExternalIdentifier(PROVIDER_MOCK, ID_TYPE_TRACK, raw["track_id"])]
    if raw.get("isrc"):
        identifiers.append(ExternalIdentifier(PROVIDER_ISRC, ID_TYPE_ISRC, raw["isrc"]))

    song = Song(
        id=raw["track_id"],
        title=raw["title"],
        artist=raw["artist"],
        external_ids=tuple(identifiers),
        album=raw.get("album"),
        artwork_url=raw.get("artwork_url"),
        external_url=raw.get("external_url"),
        release_year=raw.get("release_year"),
        duration_ms=raw.get("duration_ms"),
        explicit=bool(raw.get("explicit", False)),
        genres=tuple(raw.get("genres", ())),
    )
    popularity = SongPopularity(
        song_id=song.id,
        difficulty=difficulty_for_streams(streams),
        stream_estimate=streams,
    )
    return MockCatalogEntry(song=song, popularity=popularity, audio_file=raw["audio_file"])


@lru_cache
def load_mock_catalog(path: Path) -> MockCatalog:
    """Load and index the mock catalog from disk. Cached per path."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries: dict[str, MockCatalogEntry] = {}
    by_difficulty: dict[Difficulty, list[str]] = {tier: [] for tier in Difficulty}

    for raw_song in raw["songs"]:
        entry = _build_entry(raw_song)
        entries[entry.song.id] = entry
        by_difficulty[entry.popularity.difficulty].append(entry.song.id)

    return MockCatalog(entries=entries, by_difficulty=by_difficulty)
