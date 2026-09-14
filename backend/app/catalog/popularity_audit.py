"""The popularity audit: one report over the whole catalog.

Read only. It performs no search, no enrichment, and no writes. Every number
comes from signals already stored, so it can be run as often as you like and
always produces the same answer.
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.multisignal_report import MODELS, build_multisignal_report
from app.catalog.popularity_policy import (
    PopularityConfidence,
    PopularityInputs,
    PopularityResult,
    evaluate,
)
from app.models.enums import Difficulty

TIER_ORDER = list(Difficulty)

# The selected production model. Named here so the audit and any future
# application step cannot drift apart.
SCORE_MODEL_NAME = "F30 rescue 30%"
SCORE_MODEL = MODELS[SCORE_MODEL_NAME]


@dataclass
class AuditSummary:
    total: int = 0
    by_confidence: dict[str, int] = field(default_factory=dict)
    proposed_tiers: dict[str, int] = field(default_factory=dict)
    unchanged: int = 0
    moved_one: int = 0
    moved_two_plus: int = 0
    auto_approvable: int = 0
    review_required: int = 0


@dataclass
class PopularityAudit:
    results: list[PopularityResult] = field(default_factory=list)
    summary: AuditSummary = field(default_factory=AuditSummary)

    def where(self, **filters) -> list[PopularityResult]:
        def matches(result: PopularityResult) -> bool:
            return all(getattr(result, key) == value for key, value in filters.items())

        return [result for result in self.results if matches(result)]


async def run_popularity_audit(session: AsyncSession) -> PopularityAudit:
    """Evaluate every song in the catalog under the selected model."""
    report = await build_multisignal_report(session)
    audit = PopularityAudit()

    for row in report.rows:
        inputs = PopularityInputs(
            song_id=row.song_id,
            title=row.title,
            artist=row.artist,
            score=SCORE_MODEL(row),
            youtube_score=row.youtube_score,
            youtube_views=row.youtube_views,
            youtube_match_confidence=row.youtube_confidence,
            listener_score=row.listener_score,
            listener_count=row.listeners,
        )
        audit.results.append(evaluate(inputs, row.current_difficulty))

    # Stable ordering so two runs produce byte identical reports.
    audit.results.sort(key=lambda result: (-abs(result.tier_movement), result.song_id))

    summary = audit.summary
    summary.total = len(audit.results)

    for level in PopularityConfidence:
        summary.by_confidence[level.value] = sum(
            1 for result in audit.results if result.confidence is level
        )

    for tier in TIER_ORDER:
        summary.proposed_tiers[tier.value] = sum(
            1 for result in audit.results if result.proposed_tier is tier
        )

    for result in audit.results:
        distance = abs(result.tier_movement)
        if distance == 0:
            summary.unchanged += 1
        elif distance == 1:
            summary.moved_one += 1
        else:
            summary.moved_two_plus += 1

        if result.review_required:
            summary.review_required += 1
        else:
            summary.auto_approvable += 1

    return audit


# -- Rendering ---------------------------------------------------------------


def _score(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _tier(tier: Difficulty | None) -> str:
    return "-" if tier is None else tier.value


def _movement(value: int) -> str:
    if value == 0:
        return "same"
    return f"{'+' if value > 0 else ''}{value}"


def _table(results: list[PopularityResult], with_reason: bool = False) -> list[str]:
    if not results:
        return ["    none", ""]

    header = (
        f"    {'song':<30}{'artist':<20}{'current':<11}{'proposed':<11}"
        f"{'F30':>6}{'conf':>8}{'YT':>7}{'LB':>7}{'move':>6}"
    )
    lines = [header, "    " + "-" * (len(header) - 4)]

    for result in results:
        lines.append(
            f"    {result.title[:29]:<30}{result.artist[:19]:<20}"
            f"{_tier(result.current_tier):<11}{_tier(result.proposed_tier):<11}"
            f"{_score(result.score):>6}{result.confidence.value:>8}"
            f"{_score(result.youtube_score):>7}{_score(result.listener_score):>7}"
            f"{_movement(result.tier_movement):>6}"
        )
        if with_reason:
            lines.append(f"        why: {result.confidence_reason}")

    lines.append("")
    return lines


def render_popularity_audit(audit: PopularityAudit) -> str:
    summary = audit.summary
    lines = ["", "POPULARITY AUDIT", f"  model: {SCORE_MODEL_NAME}", ""]

    lines += [
        f"  total songs            {summary.total:>5}",
        "",
        f"  HIGH confidence        {summary.by_confidence.get('high', 0):>5}",
        f"  MEDIUM confidence      {summary.by_confidence.get('medium', 0):>5}",
        f"  LOW confidence         {summary.by_confidence.get('low', 0):>5}",
        "",
        f"  unchanged tier         {summary.unchanged:>5}",
        f"  1 tier move            {summary.moved_one:>5}",
        f"  2+ tier move           {summary.moved_two_plus:>5}",
        "",
        f"  auto approvable        {summary.auto_approvable:>5}",
        f"  review required        {summary.review_required:>5}",
        "",
        "  PROPOSED TIER DISTRIBUTION",
        "",
    ]
    for tier in TIER_ORDER:
        lines.append(f"    {tier.value:<14}{summary.proposed_tiers.get(tier.value, 0):>5}")

    auto = [result for result in audit.results if result.auto_approvable]
    review = [result for result in audit.results if result.review_required]
    low = [
        result
        for result in audit.results
        if result.confidence is PopularityConfidence.LOW
    ]
    big = [result for result in audit.results if abs(result.tier_movement) >= 2]

    lines += ["", f"TABLE A  AUTO APPROVABLE  ({len(auto)})", ""]
    lines += _table(auto)

    lines += [f"TABLE B  REVIEW REQUIRED  ({len(review)})", ""]
    lines += _table(review, with_reason=True)

    lines += [f"TABLE C  LOW CONFIDENCE  ({len(low)})", ""]
    lines += _table(low, with_reason=True)

    lines += [f"TABLE D  2+ TIER MOVES  ({len(big)})", ""]
    lines += _table(big, with_reason=True)

    return "\n".join(lines) + "\n"
