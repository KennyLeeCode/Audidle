"""How much to trust a popularity score, and what may be done with it.

Deliberately separate from scoring. F30 answers "how popular is this", and this
answers "how much should we believe that number". Mixing them would let a
distrusted song quietly get a different score, which destroys the property that
the raw signals and the formula fully determine the number.

The rules are stated in terms of what evidence exists, never in terms of which
song it is and never in terms of the tier a song currently sits in. Using the
current tier would make the system agree with itself by construction and stop it
from ever correcting an error in the seed data.

Everything here is a pure function of the stored signals, so the same catalog
always produces the same answer.
"""

from dataclasses import dataclass
from enum import Enum

from app.config.popularity import difficulty_from_score
from app.models.enums import Difficulty

TIER_ORDER = list(Difficulty)

# A YouTube upload below this many views is not measuring mainstream exposure,
# whatever else is true about it. It may be the correct recording on the artist's
# own channel and still tell us nothing about how many people have heard the
# song, which is the question difficulty actually asks.
WEAK_REPRESENTATION_VIEWS = 50_000

# Above this, an upload is carrying a real audience and can stand on its own.
STRONG_REPRESENTATION_VIEWS = 1_000_000

# Score-point gaps between the two sources. The scale is 0 to 100 and one decade
# of audience is about 18 points, so 15 is roughly "within the same order of
# magnitude" and 30 is "the sources are describing different songs".
AGREEMENT_GAP = 15.0
SEVERE_DISAGREEMENT_GAP = 30.0

# A tier move of this size needs a human to look, even on high confidence.
LARGE_MOVE = 2


class PopularityConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class PopularityInputs:
    """Everything the policy is allowed to see.

    Note what is absent: the song's current difficulty. Confidence is about the
    quality of the evidence, not about whether the answer is the one we already
    had.
    """

    song_id: str
    title: str
    artist: str
    score: float | None
    youtube_score: float | None
    youtube_views: int | None
    youtube_match_confidence: str | None
    listener_score: float | None
    listener_count: int | None


@dataclass(frozen=True)
class PopularityResult:
    song_id: str
    title: str
    artist: str
    score: float | None
    proposed_tier: Difficulty | None
    confidence: PopularityConfidence
    confidence_reason: str
    review_required: bool
    tier_movement: int
    current_tier: Difficulty | None
    youtube_score: float | None
    listener_score: float | None

    @property
    def auto_approvable(self) -> bool:
        return not self.review_required


def _gap(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return abs(left - right)


def assess_confidence(inputs: PopularityInputs) -> tuple[PopularityConfidence, str]:
    """Decide how much to trust a song's popularity score.

    Ordered from the strongest disqualifier down, so the first thing that is
    wrong is the thing reported.
    """
    has_youtube = inputs.youtube_score is not None
    has_listener = inputs.listener_score is not None
    gap = _gap(inputs.youtube_score, inputs.listener_score)

    if not has_youtube and not has_listener:
        return PopularityConfidence.LOW, "no popularity signal of any kind"

    if not has_youtube:
        # ListenBrainz alone. Its audience skews heavily toward album and indie
        # listening, so on its own it is a ranking of one community's taste
        # rather than of general recognisability.
        return (
            PopularityConfidence.LOW,
            "ListenBrainz only, no YouTube audience measurement",
        )

    views = inputs.youtube_views or 0
    if views < WEAK_REPRESENTATION_VIEWS:
        return (
            PopularityConfidence.LOW,
            f"the matched YouTube upload has only {views:,} views, too few to "
            f"measure audience exposure",
        )

    if gap is not None and gap > SEVERE_DISAGREEMENT_GAP:
        return (
            PopularityConfidence.LOW,
            f"the two sources disagree by {gap:.0f} points, which is more than "
            f"an order of magnitude of audience",
        )

    if has_listener:
        if gap is not None and gap <= AGREEMENT_GAP:
            return (
                PopularityConfidence.HIGH,
                f"YouTube and ListenBrainz agree to within {gap:.0f} points",
            )
        return (
            PopularityConfidence.MEDIUM,
            f"YouTube and ListenBrainz differ by {gap:.0f} points",
        )

    # YouTube alone. Good enough on its own when the upload is both trustworthy
    # and carrying a real audience.
    trustworthy = inputs.youtube_match_confidence in {
        "verified_official",
        "high_confidence",
        "manual",
    }
    if trustworthy and views >= STRONG_REPRESENTATION_VIEWS:
        return (
            PopularityConfidence.HIGH,
            f"a {inputs.youtube_match_confidence} YouTube upload with "
            f"{views:,} views",
        )

    return (
        PopularityConfidence.MEDIUM,
        f"YouTube only, {views:,} views, no second source to corroborate it",
    )


def tier_movement(current: Difficulty | None, proposed: Difficulty | None) -> int:
    """How many tiers a song would move. Positive is harder."""
    if current is None or proposed is None:
        return 0
    return TIER_ORDER.index(proposed) - TIER_ORDER.index(current)


def needs_review(
    confidence: PopularityConfidence, movement: int, has_score: bool
) -> bool:
    """Whether a proposed tier may be applied without a human looking.

    Scaled for a catalog far larger than the one this was calibrated on: the
    common case, a confident signal moving a song by a tier or not at all, needs
    no attention, and everything unusual surfaces.
    """
    if not has_score:
        return True
    if confidence is PopularityConfidence.LOW:
        return True
    if confidence is PopularityConfidence.MEDIUM:
        return movement != 0
    return abs(movement) >= LARGE_MOVE


def evaluate(inputs: PopularityInputs, current_tier: Difficulty | None) -> PopularityResult:
    """Score, tier, confidence, and what may be done with it."""
    confidence, reason = assess_confidence(inputs)
    proposed = difficulty_from_score(inputs.score) if inputs.score is not None else None
    movement = tier_movement(current_tier, proposed)

    return PopularityResult(
        song_id=inputs.song_id,
        title=inputs.title,
        artist=inputs.artist,
        score=inputs.score,
        proposed_tier=proposed,
        confidence=confidence,
        confidence_reason=reason,
        review_required=needs_review(confidence, movement, inputs.score is not None),
        tier_movement=movement,
        current_tier=current_tier,
        youtube_score=inputs.youtube_score,
        listener_score=inputs.listener_score,
    )
