"""In memory PopularityProvider over the mock dataset.

The mock catalog carries invented stream counts, so this provider can do what a
real dataset backed provider will do: bucket songs into difficulty tiers using
the thresholds in app/config/difficulties.py. Swapping in a provider backed by
a curated real dataset later means implementing the same two lookups.
"""

from pathlib import Path

from app.models.enums import Difficulty
from app.models.song import SongPopularity, SongSelectionCriteria
from app.providers.base import PopularityProvider
from app.providers.mock.catalog import load_mock_catalog


class MockPopularityProvider(PopularityProvider):
    """Popularity provider backed by app/data/mock_songs.json."""

    def __init__(self, catalog_path: Path) -> None:
        self._catalog = load_mock_catalog(catalog_path)

    async def get_popularity(self, song_id: str) -> SongPopularity | None:
        entry = self._catalog.get(song_id)
        return entry.popularity if entry else None

    async def get_eligible_song_ids(self, criteria: SongSelectionCriteria) -> list[str]:
        candidates = self._catalog.by_difficulty.get(criteria.difficulty, [])
        # Exclusions are applied here because they are cheap and shrink the set
        # before SongService does the metadata filtering.
        return [
            song_id for song_id in candidates if song_id not in criteria.exclude_song_ids
        ]

    async def get_difficulty(self, song_id: str) -> Difficulty | None:
        entry = self._catalog.get(song_id)
        return entry.popularity.difficulty if entry else None
