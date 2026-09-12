"""The clip stage ladder.

This is the single source of truth for how long each stage's clip is. The API
serves it to the frontend via /api/config so the two halves of the app can
never disagree about what stage 3 means.

Changing this list is the only edit required to retune the game. Stage count is
always derived from it, never hardcoded.
"""

from typing import Final

# Seconds of audio unlocked at each stage, measured from the round's start
# offset. Every stage is a strictly longer prefix of the same audio.
CLIP_STAGES: Final[list[float]] = [0.01, 0.1, 0.5, 2.0, 8.0, 15.0]

# Number of stages in a round. Derived, so adding a stage needs no other edit.
STAGE_COUNT: Final[int] = len(CLIP_STAGES)

# Index of the last stage. A wrong guess or skip here ends the round.
FINAL_STAGE_INDEX: Final[int] = STAGE_COUNT - 1

# Length of the volume ramp applied to each end of a clip, as a fraction of the
# clip itself, capped at CLIP_FADE_MAX_MS. Without this, cutting audio mid
# waveform produces a click louder and more distinctive than the music.
CLIP_FADE_RATIO: Final[float] = 0.15
CLIP_FADE_MAX_MS: Final[float] = 8.0


def clip_duration_for_stage(stage_index: int) -> float:
    """Return the unlocked clip length for a stage index.

    Raises:
        IndexError: if the stage index is outside the ladder.
    """
    if stage_index < 0 or stage_index >= STAGE_COUNT:
        raise IndexError(f"stage_index {stage_index} outside 0..{FINAL_STAGE_INDEX}")
    return CLIP_STAGES[stage_index]


def clip_fade_ms(clip_duration_seconds: float) -> float:
    """Return the fade length in milliseconds for a clip of a given length.

    Short stages get a proportionally shorter fade. A fixed 2ms ramp would be a
    fifth of the 0.01s clip, which would audibly gut it.
    """
    return min(clip_duration_seconds * 1000.0 * CLIP_FADE_RATIO, CLIP_FADE_MAX_MS)
