"""Application settings, loaded from the environment.

Every value that differs between local development and deployment lives here.
Secrets are read from the process environment or a .env file that is never
committed, so no credential is ever compiled into the codebase or shipped to
the browser.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root, resolved from this file so it works regardless of cwd.
BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    """Typed view over the environment.

    Pydantic validates on construction, so a typo in .env fails loudly at
    startup instead of silently at request time.
    """

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Provider selection -------------------------------------------------
    # These decide which concrete implementation dependencies.py wires up.
    # curated = Spotify search with metadata and difficulty from our own database
    song_provider: Literal["mock", "spotify", "curated", "audidle"] = "mock"
    popularity_provider: Literal["mock", "curated", "audidle"] = "mock"
    audio_provider: Literal["mock", "curated", "audidle"] = "mock"

    # -- Spotify ------------------------------------------------------------
    # Backend only. These must never be exposed through any API response.
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    # Restricting search to a market drops tracks unavailable there, which keeps
    # results consistent with what a player in that market would recognize.
    spotify_market: str = "US"

    # -- Catalog caching ----------------------------------------------------
    # On by default. Autocomplete fires a request per keystroke burst, and
    # Spotify's rate limits are shared across the whole app.
    catalog_cache_enabled: bool = True
    catalog_search_ttl_seconds: float = 300.0
    catalog_song_ttl_seconds: float = 3600.0

    # -- HTTP ---------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    # -- Gameplay -----------------------------------------------------------
    # Whether abandoning a round via Reroll is recorded as a loss.
    reroll_counts_as_loss: bool = False
    # How many recently played songs to exclude when drawing a new one.
    recent_songs_history_size: int = 30
    # How many candidate songs to try before giving up on finding a playable
    # one. Prevents handing the player a round with broken audio.
    max_song_selection_attempts: int = 10
    # Only offer songs that have an audio file. Without this the game draws
    # songs it cannot play and leans on the retry loop to recover, which fails
    # once most of the catalog lacks audio.
    require_audio_for_selection: bool = True
    # Seconds the reveal stays up before Auto Next fires, when it is enabled.
    auto_next_delay_seconds: float = 8.0

    # -- Database -----------------------------------------------------------
    # SQLite by default, so local development needs no setup. The models use no
    # SQLite specific types, so Postgres is a URL change plus asyncpg.
    database_url: str = "sqlite+aiosqlite:///./audidle.db"
    database_echo: bool = False

    # -- Paths --------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return BACKEND_ROOT / "app" / "data"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list, since env vars can only carry strings."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def spotify_configured(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret)


@lru_cache
def get_settings() -> Settings:
    """Return the process wide settings singleton.

    Cached so the .env file is read once and so FastAPI's dependency system can
    inject the same instance everywhere.
    """
    return Settings()
