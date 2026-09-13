"""Turning raw popularity signals into an Audidle score.

One place, on purpose. The raw measurements are the expensive thing to collect
and the formula is the cheap thing to change, so signals are stored untouched in
song_popularity_signals and this module converts them on demand. Retuning the
curve is an edit here plus a recompute, never a re-collection.

Why logarithmic. Music popularity spans about seven orders of magnitude, from a
few thousand views to several billion. On a linear scale every song below a
hundred million rounds to zero and the tiers below Easy become meaningless. On a
log scale each tenfold increase moves the score by a fixed amount, which matches
how recognizability actually behaves: the gap between 10k and 100k views is a
much bigger difference in "would you know this" than the gap between 1.0 and 1.1
billion.

Nothing here is final. The anchors below are a starting point to be calibrated
against real data before the difficulty thresholds are changed.
"""

import math
from typing import Final

from app.models.enums import Difficulty

# The score scale. 0 means unheard of, 100 means unmissable.
SCORE_MIN: Final[float] = 0.0
SCORE_MAX: Final[float] = 100.0

# The view counts anchoring the ends of the curve. Chosen so the usable range
# covers what real music actually does: below the floor a song is effectively
# unknown, above the ceiling it is saturated and further views add nothing to
# how recognizable it is.
VIEW_FLOOR: Final[int] = 10_000
VIEW_CEILING: Final[int] = 3_000_000_000

# ListenBrainz runs several orders of magnitude smaller than YouTube, so it gets
# its own anchors. Chosen as round numbers bracketing the observed range rather
# than fitted to it: across the calibration set unique listeners ran from 7 to
# 269k and listens from 7 to 4.1M. The ceilings sit above both so a future
# bigger song is ranked rather than clipped.
LISTENER_FLOOR: Final[int] = 50
LISTENER_CEILING: Final[int] = 500_000

LISTEN_FLOOR: Final[int] = 500
LISTEN_CEILING: Final[int] = 10_000_000


def log_normalize(value: int, floor: int, ceiling: int) -> float:
    """Map a count onto 0 to 100 logarithmically.

    Values at or below the floor score 0, values at or above the ceiling score
    100, and each tenfold increase between them moves the score by a constant
    amount.
    """
    if value <= floor:
        return SCORE_MIN
    if value >= ceiling:
        return SCORE_MAX

    span = math.log10(ceiling) - math.log10(floor)
    position = math.log10(value) - math.log10(floor)
    return round(SCORE_MAX * position / span, 2)


def score_from_youtube_views(views: int) -> float:
    """The primary Audidle popularity score.

    YouTube is the main signal because it is the largest publicly readable
    audience measure available. It is explicitly not a stand-in for Spotify
    stream counts, and the score is a ranking of recognizability rather than a
    claim about plays anywhere.
    """
    return log_normalize(views, VIEW_FLOOR, VIEW_CEILING)


def score_from_listener_count(listeners: int) -> float:
    """The ListenBrainz signal that matters most for Audidle.

    Distinct people, not plays. The game asks whether someone would recognize a
    song, and a thousand plays by one devoted fan says much less about that than
    a thousand different people playing it once.
    """
    return log_normalize(listeners, LISTENER_FLOOR, LISTENER_CEILING)


def score_from_listen_count(listens: int) -> float:
    """Total ListenBrainz plays. Kept, but weighted below unique listeners."""
    return log_normalize(listens, LISTEN_FLOOR, LISTEN_CEILING)


# Provisional score bands. Deliberately not wired into difficulty assignment
# until they have been checked against the calibration report.
PROVISIONAL_THRESHOLDS: Final[dict[Difficulty, tuple[float, float]]] = {
    Difficulty.EASY: (80.0, 100.0),
    Difficulty.MEDIUM: (68.0, 80.0),
    Difficulty.HARD: (55.0, 68.0),
    Difficulty.EXPERT: (40.0, 55.0),
    Difficulty.IMPOSSIBLE: (0.0, 40.0),
}


def difficulty_from_score(
    score: float, thresholds: dict[Difficulty, tuple[float, float]] | None = None
) -> Difficulty:
    """Bucket a popularity score into a difficulty tier.

    Takes the threshold table as an argument so the calibration report can try
    several without changing global state.
    """
    table = thresholds or PROVISIONAL_THRESHOLDS
    for difficulty, (low, high) in table.items():
        if low <= score <= high:
            return difficulty
    return Difficulty.IMPOSSIBLE


def views_for_score(score: float) -> int:
    """Invert the curve, for describing what a threshold means in views."""
    if score <= SCORE_MIN:
        return VIEW_FLOOR
    if score >= SCORE_MAX:
        return VIEW_CEILING

    span = math.log10(VIEW_CEILING) - math.log10(VIEW_FLOOR)
    return int(10 ** (math.log10(VIEW_FLOOR) + span * score / SCORE_MAX))
