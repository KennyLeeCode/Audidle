"""Tests for the stage ladder configuration.

These guard the promise that the ladder is genuinely configurable: changing
CLIP_STAGES must retune the game without any other edit.
"""

import pytest

from app.config.stages import (
    CLIP_STAGES,
    FINAL_STAGE_INDEX,
    STAGE_COUNT,
    clip_duration_for_stage,
    clip_fade_ms,
)


def test_stage_count_is_derived_not_hardcoded():
    assert STAGE_COUNT == len(CLIP_STAGES)
    assert FINAL_STAGE_INDEX == len(CLIP_STAGES) - 1


def test_stages_are_strictly_increasing():
    """Every stage must be a longer prefix than the one before it."""
    assert CLIP_STAGES == sorted(CLIP_STAGES)
    assert len(set(CLIP_STAGES)) == len(CLIP_STAGES)


def test_clip_duration_lookup_matches_the_ladder():
    for index, duration in enumerate(CLIP_STAGES):
        assert clip_duration_for_stage(index) == duration


@pytest.mark.parametrize("index", [-1, STAGE_COUNT])
def test_out_of_range_stage_raises(index):
    with pytest.raises(IndexError):
        clip_duration_for_stage(index)


def test_fade_scales_with_clip_length():
    """A fixed fade would gut the shortest stage, so it is proportional."""
    shortest = clip_fade_ms(CLIP_STAGES[0])
    longest = clip_fade_ms(CLIP_STAGES[-1])

    assert 0 < shortest < longest
    # Never long enough to consume the 0.01s clip.
    assert shortest < CLIP_STAGES[0] * 1000 / 2
