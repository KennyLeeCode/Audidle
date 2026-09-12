"""Wire schemas for the client bootstrap config.

The frontend renders its stage pills and difficulty tabs from this, rather than
from its own copy of the numbers. That is what guarantees the two halves of the
app can never disagree about what stage 3 means, and it means retuning the game
is a backend only change.
"""

from pydantic import BaseModel, Field

from app.config.difficulties import DIFFICULTIES, DifficultyConfig
from app.config.stages import CLIP_STAGES, STAGE_COUNT, clip_fade_ms
from app.models.enums import Difficulty


class StageConfigResponse(BaseModel):
    """One stage on the ladder."""

    index: int
    duration: float = Field(description="Seconds of audio unlocked at this stage")
    label: str = Field(description="Pre-formatted label, for example 0.5s")
    fade_ms: float = Field(
        description="Volume ramp applied to each end of the clip, to avoid a click"
    )


class DifficultyConfigResponse(BaseModel):
    """One difficulty tier, including the accent colour the UI should use."""

    key: Difficulty
    label: str
    color: str
    description: str
    stream_floor: int
    stream_ceiling: int | None

    @classmethod
    def from_domain(cls, config: DifficultyConfig) -> "DifficultyConfigResponse":
        return cls(
            key=config.key,
            label=config.label,
            color=config.color,
            description=config.description,
            stream_floor=config.stream_floor,
            stream_ceiling=config.stream_ceiling,
        )


class GameConfigResponse(BaseModel):
    """Everything the client needs before it can render a game."""

    stages: list[StageConfigResponse]
    total_stages: int
    difficulties: list[DifficultyConfigResponse]
    auto_next_delay_seconds: float
    reroll_counts_as_loss: bool


def _format_duration(seconds: float) -> str:
    """Render a stage duration the way the UI shows it.

    Done server side so the label and the value can never drift apart, and so
    adding a 0.05s stage does not need a frontend formatting change.
    """
    if seconds >= 1:
        text = f"{seconds:g}"
    else:
        text = f"{seconds:.2f}".rstrip("0").rstrip(".")
    return f"{text}s"


def build_game_config(
    auto_next_delay_seconds: float, reroll_counts_as_loss: bool
) -> GameConfigResponse:
    """Assemble the bootstrap config from the central configuration modules."""
    return GameConfigResponse(
        stages=[
            StageConfigResponse(
                index=index,
                duration=duration,
                label=_format_duration(duration),
                fade_ms=clip_fade_ms(duration),
            )
            for index, duration in enumerate(CLIP_STAGES)
        ],
        total_stages=STAGE_COUNT,
        difficulties=[
            DifficultyConfigResponse.from_domain(config) for config in DIFFICULTIES.values()
        ],
        auto_next_delay_seconds=auto_next_delay_seconds,
        reroll_counts_as_loss=reroll_counts_as_loss,
    )
