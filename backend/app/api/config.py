"""Client bootstrap config route.

The frontend fetches this once on load and renders its stage pills and
difficulty tabs from the response. Serving the numbers rather than duplicating
them in the frontend is what makes retuning the game a single backend edit.
"""

from fastapi import APIRouter

from app.dependencies import SettingsDep
from app.schemas.config import GameConfigResponse, build_game_config

router = APIRouter(tags=["config"])


@router.get("/config", response_model=GameConfigResponse, summary="Client bootstrap config")
async def get_game_config(settings: SettingsDep) -> GameConfigResponse:
    """Return the stage ladder, difficulty tiers, and gameplay toggles."""
    return build_game_config(
        auto_next_delay_seconds=settings.auto_next_delay_seconds,
        reroll_counts_as_loss=settings.reroll_counts_as_loss,
    )
