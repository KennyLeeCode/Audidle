"""Composition root.

Every provider and service is constructed here and nowhere else. This is the
one file that knows which concrete implementation is active, which is what
makes the provider abstraction actually pay off: switching SONG_PROVIDER from
mock to spotify in .env changes the factory below and touches no other module.

Singletons are cached with lru_cache because the providers hold loaded catalogs
and the repository holds live rounds. Rebuilding them per request would drop
every in flight round on the floor.
"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings, get_settings
from app.core.errors import ProviderConfigurationError
from app.database.database import build_engine, build_session_factory
from app.providers.base import AudioProvider, PopularityProvider, SongCatalogProvider
from app.providers.caching import CachedSongCatalogProvider
from app.providers.database.audidle_catalog import (
    AudidleAudioProvider,
    AudidlePopularityProvider,
    AudidleSongCatalogProvider,
)
from app.providers.database.db_audio_provider import DbAudioProvider
from app.providers.database.db_catalog import (
    CompositeSongCatalogProvider,
    DbPopularityProvider,
    DbSongCatalogProvider,
)
from app.providers.mock import (
    MockAudioProvider,
    MockPopularityProvider,
    MockSongProvider,
)
from app.providers.musicbrainz.musicbrainz_provider import MusicBrainzProvider
from app.providers.spotify.spotify_client import SpotifyClient
from app.providers.spotify.spotify_song_provider import SpotifySongProvider
from app.repositories.round_repository import InMemoryRoundRepository, RoundRepository
from app.services.game_service import GameService
from app.services.recent_songs import RecentSongsTracker
from app.services.song_service import SongService

logger = logging.getLogger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]


# -- Providers --------------------------------------------------------------


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Database session factory.

    Cached because the engine owns a connection pool. One per process.
    """
    settings = get_settings()
    engine = build_engine(settings.database_url, echo=settings.database_echo)
    return build_session_factory(engine)


@lru_cache
def get_spotify_client() -> SpotifyClient:
    """Build the shared Spotify HTTP client.

    Cached because it holds a connection pool and a cached access token. One
    instance per process, closed on shutdown by the lifespan handler.
    """
    settings = get_settings()
    return SpotifyClient(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
    )


@lru_cache
def get_musicbrainz_provider() -> MusicBrainzProvider:
    """Build the MusicBrainz client.

    Used only by ingestion and enrichment. Nothing on the round creation path
    touches it, which is what keeps starting a round a local operation.
    """
    settings = get_settings()
    return MusicBrainzProvider(
        user_agent=settings.musicbrainz_user_agent,
        contact=settings.musicbrainz_contact,
        requests_per_second=settings.musicbrainz_rate_limit,
    )


@lru_cache
def get_song_catalog_provider() -> SongCatalogProvider:
    """Build the active catalog provider, wrapped in caching if enabled."""
    settings = get_settings()

    if settings.song_provider == "mock":
        provider: SongCatalogProvider = MockSongProvider(settings.data_dir / "mock_songs.json")
    elif settings.song_provider == "audidle":
        # The real catalog path. Spotify supplies search so any song can be
        # guessed, the Audidle database supplies identity and metadata, and
        # guesses are resolved back to Audidle songs server side.
        catalog = AudidleSongCatalogProvider(get_session_factory())
        if settings.spotify_configured:
            provider = CompositeSongCatalogProvider(
                search_provider=SpotifySongProvider(
                    client=get_spotify_client(), market=settings.spotify_market
                ),
                metadata_provider=catalog,
            )
        else:
            # Without Spotify the game still runs, guessing is just limited to
            # songs we hold. Proving that is the point of the abstraction.
            logger.warning(
                "SONG_PROVIDER=audidle without Spotify credentials, "
                "search is limited to the local catalog"
            )
            provider = catalog
    elif settings.song_provider in ("spotify", "curated"):
        if not settings.spotify_configured:
            # Failing at startup rather than on the first search, so a missing
            # credential is obvious immediately instead of mid game.
            raise ProviderConfigurationError(
                f"SONG_PROVIDER={settings.song_provider} requires SPOTIFY_CLIENT_ID "
                f"and SPOTIFY_CLIENT_SECRET"
            )
        spotify = SpotifySongProvider(
            client=get_spotify_client(),
            market=settings.spotify_market,
        )
        if settings.song_provider == "spotify":
            provider = spotify
        else:
            # Search the whole Spotify catalog so any song can be guessed, but
            # resolve metadata locally so the hot path survives an outage.
            provider = CompositeSongCatalogProvider(
                search_provider=spotify,
                metadata_provider=DbSongCatalogProvider(get_session_factory()),
            )
    else:
        raise ProviderConfigurationError(f"unknown SONG_PROVIDER '{settings.song_provider}'")

    # Caching is applied as a wrapper rather than built into either provider, so
    # the policy lives in one place and covers whichever one is active.
    if settings.catalog_cache_enabled:
        return CachedSongCatalogProvider(
            provider,
            search_ttl_seconds=settings.catalog_search_ttl_seconds,
            song_ttl_seconds=settings.catalog_song_ttl_seconds,
        )
    return provider


@lru_cache
def get_popularity_provider() -> PopularityProvider:
    """Build the active popularity provider."""
    settings = get_settings()

    if settings.popularity_provider == "mock":
        return MockPopularityProvider(settings.data_dir / "mock_songs.json")

    if settings.popularity_provider == "audidle":
        return AudidlePopularityProvider(get_session_factory())

    if settings.popularity_provider == "curated":
        return DbPopularityProvider(
            get_session_factory(),
            require_audio=settings.require_audio_for_selection,
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

    if settings.audio_provider == "audidle":
        return AudidleAudioProvider(get_session_factory(), settings.audio_dir)

    if settings.audio_provider == "curated":
        return DbAudioProvider(get_session_factory(), settings.audio_dir)

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


def validate_provider_combination() -> None:
    """Reject provider combinations that cannot produce a playable round.

    Called at startup, because the failure this catches is confusing at request
    time. The mock popularity provider draws from mock track ids, so pairing it
    with the Spotify catalog means selection asks Spotify to resolve ids like
    "mock012". Spotify returns nothing, and every round fails with a misleading
    "no eligible song" error rather than naming the real cause.

    The Spotify catalog therefore needs a popularity source built over real
    Spotify track ids, which is the next piece of work.
    """
    settings = get_settings()

    if (
        settings.song_provider in ("spotify", "curated", "audidle")
        and settings.popularity_provider == "mock"
    ):
        raise ProviderConfigurationError(
            "SONG_PROVIDER=spotify cannot be used with POPULARITY_PROVIDER=mock. The mock "
            "popularity data indexes mock track ids, which the Spotify catalog cannot "
            "resolve, so no round could ever be created. Either run both as mock, or "
            "supply a popularity dataset built over real Spotify track ids."
        )

    if settings.popularity_provider == "curated" and settings.song_provider == "mock":
        raise ProviderConfigurationError(
            "POPULARITY_PROVIDER=curated indexes real Spotify track ids, which the mock "
            "catalog cannot resolve. Use SONG_PROVIDER=curated."
        )


async def shutdown_providers() -> None:
    """Release provider resources on application shutdown.

    Only the Spotify client holds anything that needs closing, and only if it
    was ever constructed. Checking the cache rather than calling the factory
    avoids building a client purely in order to close it.
    """
    if get_spotify_client.cache_info().currsize:
        await get_spotify_client().aclose()
    if get_musicbrainz_provider.cache_info().currsize:
        await get_musicbrainz_provider().aclose()
