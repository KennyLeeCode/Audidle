"""Shared test fixtures.

Services are constructed directly rather than through the FastAPI dependency
system, so the rule tests exercise the state machine with no HTTP layer in the
way. The API tests use a TestClient with the same providers.
"""

import random

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app
from app.providers.mock import (
    MockAudioProvider,
    MockPopularityProvider,
    MockSongProvider,
)
from app.repositories.round_repository import InMemoryRoundRepository
from app.services.game_service import GameService
from app.services.recent_songs import RecentSongsTracker
from app.services.song_service import SongService


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def catalog_path(settings):
    return settings.data_dir / "mock_songs.json"


@pytest.fixture
def song_service(settings, catalog_path) -> SongService:
    return SongService(
        catalog=MockSongProvider(catalog_path),
        popularity=MockPopularityProvider(catalog_path),
        audio=MockAudioProvider(catalog_path, settings.audio_dir),
        # Seeded so a failing test reproduces on the next run.
        rng=random.Random(1234),
    )


@pytest.fixture
def game_service(song_service) -> GameService:
    return GameService(
        song_service=song_service,
        round_repository=InMemoryRoundRepository(),
        recent_songs=RecentSongsTracker(history_size=10),
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
