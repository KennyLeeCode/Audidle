"""Liveness and provider status.

Useful during development for confirming which providers are actually wired up,
without having to read the logs or guess from behaviour.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.dependencies import SettingsDep

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    song_provider: str
    popularity_provider: str
    audio_provider: str
    spotify_configured: bool


@router.get("/health", response_model=HealthResponse, summary="Service health")
async def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        song_provider=settings.song_provider,
        popularity_provider=settings.popularity_provider,
        audio_provider=settings.audio_provider,
        # Reports only whether credentials are present. Never their values.
        spotify_configured=settings.spotify_configured,
    )
