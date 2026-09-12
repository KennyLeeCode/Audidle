"""Composition root.

Every provider and service is constructed here and nowhere else. This is the
one file that knows which concrete implementation is active, which is what
makes the provider abstraction actually pay off: switching SONG_PROVIDER from
mock to spotify in .env changes the factory below and touches no other module.

Singletons are cached with lru_cache because the providers hold loaded catalogs
and the repository holds live rounds. Rebuilding them per request would drop
every in flight round on the floor.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.config.settings import Settings, get_settings
from app.core.errors import ProviderConfigurationError
from app.providers.base import AudioProvider, PopularityProvider, SongCatalogProvider
from app.providers.mock import (
    MockAudioProvider,
    MockPopularityProvider,
    MockSongProvider,
)
from app.repositories.round_repository import InMemoryRoundRepository, RoundRepository
from app.services.game_service import GameService
from app.services.recent_songs import RecentSongsTracker
from app.services.song_service import SongService

SettingsDep = Annotated[Settings, Depends(get_settings)]


# -- Providers --------------------------------------------------------------


@lru_cache
def get_song_catalog_provider() -> SongCatalogProvider:
    """Build the active catalog provider."""
    settings = get_settings()

    if settings.song_provider == "mock":
        return MockSongProvider(settings.data_dir / "mock_songs.json")

    if settings.song_provider == "spotify":
        # Deliberately not implemented yet. Failing loudly at startup is far
        # better than silently falling back to mock data and letting someone
        # believe they are looking at real Spotify results.
        raise ProviderConfigurationError(
            "SONG_PROVIDER=spotify is not implemented yet, use SONG_PROVIDER=mock"
        )

    raise ProviderConfigurationError(f"unknown SONG_PROVIDER '{settings.song_provider}'")


@lru_cache
def get_popularity_provider() -> PopularityProvider:
    """Build the active popularity provider."""
    settings = get_settings()

    if settings.popularity_provider == "mock":
        return MockPopularityProvider(settings.data_dir / "mock_songs.json")

    if settings.popularity_provider == "static":
        raise ProviderConfigurationError(
            "POPULARITY_PROVIDER=static needs a curated stream count dataset, "
            "which is not present yet. Use POPULARITY_PROVIDER=mock"
        )

    raise ProviderConfigurationError(
        f"unknown POPULARITY_PROVIDER '{settings.popularity_provider}'"
    )


@lru_cache
def get_audio_provider() -> AudioProvider:
    """Build the active audio provider."""
    settings = get_settings()

    if settings.audio_provider == "mock":
        return MockAudioProvider(settings.data_dir / "mock_songs.json", settings.audio_dir)

    raise ProviderConfigurationError(f"unknown AUDIO_PROVIDER '{settings.audio_provider}'")


# -- Repositories and services ----------------------------------------------


@lru_cache
def get_round_repository() -> RoundRepository:
    """Round storage. In memory for now, see the class docstring for limits."""
    return InMemoryRoundRepository()


@lru_cache
def get_recent_songs_tracker() -> RecentSongsTracker:
    settings = get_settings()
    return RecentSongsTracker(history_size=settings.recent_songs_history_size)


@lru_cache
def get_song_service() -> SongService:
    settings = get_settings()
    return SongService(
        catalog=get_song_catalog_provider(),
        popularity=get_popularity_provider(),
        audio=get_audio_provider(),
        max_attempts=settings.max_song_selection_attempts,
    )


@lru_cache
def get_game_service() -> GameService:
    settings = get_settings()
    return GameService(
        song_service=get_song_service(),
        round_repository=get_round_repository(),
        recent_songs=get_recent_songs_tracker(),
        reroll_counts_as_loss=settings.reroll_counts_as_loss,
    )


GameServiceDep = Annotated[GameService, Depends(get_game_service)]
SongServiceDep = Annotated[SongService, Depends(get_song_service)]
