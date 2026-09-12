"""In memory SongCatalogProvider over the mock dataset.

Exists so the entire game is playable and testable with zero external
dependencies and zero credentials. Its search is a simple substring and token
match, which is enough to drive the autocomplete UI.
"""

from pathlib import Path

from app.models.song import Song
from app.providers.base import SongCatalogProvider
from app.providers.mock.catalog import load_mock_catalog


class MockSongProvider(SongCatalogProvider):
    """Catalog provider backed by app/data/mock_songs.json."""

    def __init__(self, catalog_path: Path) -> None:
        self._catalog = load_mock_catalog(catalog_path)

    async def search(self, query: str, limit: int = 10) -> list[Song]:
        normalized = query.strip().lower()
        if not normalized:
            return []

        scored: list[tuple[int, Song]] = []
        for song in self._catalog.songs:
            score = self._score(song, normalized)
            if score > 0:
                scored.append((score, song))

        # Highest score first, then alphabetical so results are deterministic.
        scored.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [song for _, song in scored[:limit]]

    async def get_song(self, track_id: str) -> Song | None:
        entry = self._catalog.get(track_id)
        return entry.song if entry else None

    async def get_songs(self, track_ids: list[str]) -> list[Song]:
        found = [self._catalog.get(track_id) for track_id in track_ids]
        return [entry.song for entry in found if entry is not None]

    @staticmethod
    def _score(song: Song, query: str) -> int:
        """Rank a song against a lowercase query. Zero means no match."""
        title = song.title.lower()
        artist = song.artist.lower()

        if title.startswith(query):
            return 100
        if artist.startswith(query):
            return 80
        if query in title:
            return 60
        if query in artist:
            return 40
        if song.album and query in song.album.lower():
            return 20
        return 0
