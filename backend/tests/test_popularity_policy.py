"""Tests for the popularity confidence policy.

The policy must be a pure function of the stored evidence. Two properties
matter most and both are easy to lose:

  - It never looks at the song's current tier. Doing so would make the system
    agree with itself and stop it ever correcting a seed data error.
  - It never looks at which song it is. Every rule is stated in terms of what
    evidence exists.
"""

import pytest

from app.catalog.popularity_policy import (
    AGREEMENT_GAP,
    STRONG_REPRESENTATION_VIEWS,
    WEAK_REPRESENTATION_VIEWS,
    PopularityConfidence,
    PopularityInputs,
    assess_confidence,
    evaluate,
    needs_review,
    tier_movement,
)
from app.models.enums import Difficulty


def inputs(**overrides) -> PopularityInputs:
    base = {
        "song_id": "s",
        "title": "A Song",
        "artist": "An Artist",
        "score": 70.0,
        "youtube_score": 70.0,
        "youtube_views": 10_000_000,
        "youtube_match_confidence": "verified_official",
        "listener_score": 70.0,
        "listener_count": 20_000,
    }
    return PopularityInputs(**{**base, **overrides})


# -- HIGH --------------------------------------------------------------------


def test_both_sources_agreeing_is_high():
    confidence, reason = assess_confidence(inputs(youtube_score=70.0, listener_score=78.0))

    assert confidence is PopularityConfidence.HIGH
    assert "agree" in reason


def test_a_strong_youtube_only_match_is_high():
    confidence, _ = assess_confidence(
        inputs(listener_score=None, listener_count=None, youtube_views=1_200_000_000)
    )

    assert confidence is PopularityConfidence.HIGH


# -- MEDIUM ------------------------------------------------------------------


def test_sources_differing_somewhat_is_medium():
    confidence, _ = assess_confidence(inputs(youtube_score=50.0, listener_score=72.0))

    assert confidence is PopularityConfidence.MEDIUM


def test_youtube_only_with_a_modest_audience_is_medium():
    confidence, reason = assess_confidence(
        inputs(listener_score=None, listener_count=None, youtube_views=200_000)
    )

    assert confidence is PopularityConfidence.MEDIUM
    assert "corroborate" in reason


# -- LOW ---------------------------------------------------------------------


def test_listenbrainz_only_is_low():
    confidence, reason = assess_confidence(
        inputs(youtube_score=None, youtube_views=None, youtube_match_confidence=None)
    )

    assert confidence is PopularityConfidence.LOW
    assert "ListenBrainz only" in reason


def test_a_tiny_youtube_audience_is_low():
    """A valid upload can still be a meaningless measurement."""
    confidence, reason = assess_confidence(inputs(youtube_views=535))

    assert confidence is PopularityConfidence.LOW
    assert "535" in reason


def test_severely_contradictory_sources_are_low():
    confidence, reason = assess_confidence(inputs(youtube_score=95.0, listener_score=17.0))

    assert confidence is PopularityConfidence.LOW
    assert "disagree" in reason


def test_no_signals_at_all_is_low():
    confidence, _ = assess_confidence(
        inputs(
            score=None,
            youtube_score=None,
            youtube_views=None,
            youtube_match_confidence=None,
            listener_score=None,
            listener_count=None,
        )
    )

    assert confidence is PopularityConfidence.LOW


@pytest.mark.parametrize(
    ("views", "expected"),
    [
        (WEAK_REPRESENTATION_VIEWS - 1, PopularityConfidence.LOW),
        (WEAK_REPRESENTATION_VIEWS, PopularityConfidence.HIGH),
    ],
)
def test_the_weak_representation_boundary(views, expected):
    assert assess_confidence(inputs(youtube_views=views))[0] is expected


def test_the_strong_representation_boundary():
    base = {"listener_score": None, "listener_count": None}
    below = assess_confidence(inputs(youtube_views=STRONG_REPRESENTATION_VIEWS - 1, **base))
    at = assess_confidence(inputs(youtube_views=STRONG_REPRESENTATION_VIEWS, **base))

    assert below[0] is PopularityConfidence.MEDIUM
    assert at[0] is PopularityConfidence.HIGH


def test_the_agreement_boundary():
    inside = assess_confidence(inputs(youtube_score=50.0, listener_score=50.0 + AGREEMENT_GAP))
    outside = assess_confidence(
        inputs(youtube_score=50.0, listener_score=50.0 + AGREEMENT_GAP + 0.1)
    )

    assert inside[0] is PopularityConfidence.HIGH
    assert outside[0] is PopularityConfidence.MEDIUM


# -- The current tier must not influence confidence -------------------------


@pytest.mark.parametrize("tier", list(Difficulty))
def test_confidence_ignores_the_current_tier(tier):
    """Using it would make the system agree with itself by construction."""
    results = {evaluate(inputs(), tier).confidence for tier in Difficulty}

    assert len(results) == 1


def test_confidence_ignores_the_song_identity():
    first = assess_confidence(inputs(title="Redbone", artist="Childish Gambino"))
    second = assess_confidence(inputs(title="Anything", artist="Anyone"))

    assert first == second


# -- Application policy ------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "movement", "expected"),
    [
        (PopularityConfidence.HIGH, 0, False),
        (PopularityConfidence.HIGH, 1, False),
        (PopularityConfidence.HIGH, -1, False),
        (PopularityConfidence.HIGH, 2, True),
        (PopularityConfidence.HIGH, -3, True),
        (PopularityConfidence.MEDIUM, 0, False),
        (PopularityConfidence.MEDIUM, 1, True),
        (PopularityConfidence.MEDIUM, -1, True),
        (PopularityConfidence.LOW, 0, True),
        (PopularityConfidence.LOW, 1, True),
    ],
)
def test_review_policy(confidence, movement, expected):
    assert needs_review(confidence, movement, has_score=True) is expected


def test_a_song_with_no_score_always_needs_review():
    assert needs_review(PopularityConfidence.HIGH, 0, has_score=False) is True


def test_low_confidence_is_never_auto_approvable():
    result = evaluate(inputs(youtube_views=100), Difficulty.EASY)

    assert result.confidence is PopularityConfidence.LOW
    assert result.review_required
    assert not result.auto_approvable


def test_a_low_confidence_song_keeps_its_score_and_proposal():
    """No replacement score is invented for distrusted songs."""
    result = evaluate(inputs(score=48.0, youtube_views=100), Difficulty.EASY)

    assert result.score == 48.0
    assert result.proposed_tier is Difficulty.EXPERT


# -- Movement ----------------------------------------------------------------


def test_tier_movement_is_signed():
    assert tier_movement(Difficulty.EASY, Difficulty.HARD) == 2
    assert tier_movement(Difficulty.HARD, Difficulty.EASY) == -2
    assert tier_movement(Difficulty.EASY, Difficulty.EASY) == 0
    assert tier_movement(None, Difficulty.EASY) == 0


# -- Determinism -------------------------------------------------------------


def test_evaluation_is_deterministic():
    first = evaluate(inputs(), Difficulty.MEDIUM)
    second = evaluate(inputs(), Difficulty.MEDIUM)

    assert first == second
