"""Shared test fixtures.

Services are constructed directly rather than through the FastAPI dependency
system, so the rule tests exercise the state machine with no HTTP layer in the
way. The API tests use a TestClient with the same providers.
"""

import os
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


@pytest.fixture(autouse=True)
def force_mock_providers(monkeypatch):
    """Pin the API tests to the mock providers.

    Without this the suite reads whatever .env happens to say, so switching the
    local default to the Audidle catalog would break tests that have nothing to
    do with the change. Tests should not depend on a developer's environment.
    """
    monkeypatch.setitem(os.environ, "SONG_PROVIDER", "mock")
    monkeypatch.setitem(os.environ, "POPULARITY_PROVIDER", "mock")
    monkeypatch.setitem(os.environ, "AUDIO_PROVIDER", "mock")

    # Every provider factory is lru_cached, so the caches have to be cleared
    # for the new settings to take effect.
    from app import dependencies

    get_settings.cache_clear()
    for factory in (
        dependencies.get_song_catalog_provider,
        dependencies.get_popularity_provider,
        dependencies.get_audio_provider,
        dependencies.get_song_service,
        dependencies.get_game_service,
        dependencies.get_round_repository,
        dependencies.get_recent_songs_tracker,
    ):
        factory.cache_clear()

    yield

    get_settings.cache_clear()


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
