"""The popularity calibration report.

Compares collected YouTube view counts against the difficulty tiers the catalog
currently has, so the scoring curve and its thresholds can be chosen from real
data rather than guessed at.

Reporting only. Nothing here writes a difficulty or changes the game.
"""

import statistics
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.popularity import (
    PROVISIONAL_THRESHOLDS,
    difficulty_from_score,
    score_from_youtube_views,
    views_for_score,
)
from app.database.catalog_models import (
    CatalogSong,
    ExternalIdentifierRow,
    SongEligibility,
    SongPopularitySignal,
)
from app.models.enums import Difficulty

TIER_ORDER = list(Difficulty)


@dataclass
class SongPopularity:
    song_id: str
    title: str
    artist: str
    views: int
    score: float
    current_difficulty: Difficulty | None
    proposed_difficulty: Difficulty
    match_confidence: str

    @property
    def moved(self) -> bool:
        return (
            self.current_difficulty is not None
            and self.current_difficulty is not self.proposed_difficulty
        )

    @property
    def distance(self) -> int:
        """How many tiers a song would move, signed. Negative means easier."""
        if self.current_difficulty is None:
            return 0
        return TIER_ORDER.index(self.proposed_difficulty) - TIER_ORDER.index(
            self.current_difficulty
        )


@dataclass
class PopularityReport:
    rows: list[SongPopularity] = field(default_factory=list)
    total_songs: int = 0
    with_video: int = 0
    with_views: int = 0
    unresolved: int = 0

    @property
    def views(self) -> list[int]:
        return [row.views for row in self.rows]


async def build_popularity_report(session: AsyncSession) -> PopularityReport:
    """Gather every collected view count alongside the current difficulty."""
    report = PopularityReport()

    songs = {
        song.id: song for song in (await session.execute(select(CatalogSong))).scalars()
    }
    report.total_songs = len(songs)

    video_rows = (
        await session.execute(
            select(
                ExternalIdentifierRow.song_id, ExternalIdentifierRow.confidence
            ).where(
                ExternalIdentifierRow.provider == "youtube",
                ExternalIdentifierRow.identifier_type == "video_id",
            )
        )
    ).all()
    confidence_by_song = dict(video_rows)
    report.with_video = len(confidence_by_song)

    signals = (
        await session.execute(
            select(SongPopularitySignal).where(
                SongPopularitySignal.source == "youtube",
                SongPopularitySignal.metric == "view_count",
            )
        )
    ).scalars().all()
    report.with_views = len(signals)

    for signal in signals:
        song = songs.get(signal.song_id)
        if song is None:
            continue

        eligibility = await session.get(SongEligibility, song.id)
        current = (
            Difficulty(eligibility.difficulty)
            if eligibility and eligibility.difficulty
            else None
        )

        score = score_from_youtube_views(signal.value)
        report.rows.append(
            SongPopularity(
                song_id=song.id,
                title=song.title,
                artist=song.artist_credit,
                views=signal.value,
                score=score,
                current_difficulty=current,
                proposed_difficulty=difficulty_from_score(score),
                match_confidence=confidence_by_song.get(song.id, "unknown"),
            )
        )

    report.rows.sort(key=lambda row: -row.views)
    return report


def _fmt(value: float) -> str:
    """Compact view counts, since billions are common."""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= limit:
            return f"{value / limit:.1f}{suffix}"
    return str(int(value))


def render_report(report: PopularityReport) -> str:
    """Format the calibration report."""
    lines: list[str] = ["", "YOUTUBE POPULARITY CALIBRATION", ""]

    lines.append(f"  songs in catalog        {report.total_songs:>8}")
    lines.append(f"  with a matched video    {report.with_video:>8}")
    lines.append(f"  with a view count       {report.with_views:>8}")

    if not report.rows:
        lines.append("")
        lines.append("  No view counts collected yet. Run enrich-youtube first.")
        return "\n".join(lines) + "\n"

    views = report.views
    lines += [
        "",
        "  VIEW COUNT DISTRIBUTION",
        f"    median     {_fmt(statistics.median(views)):>10}",
        f"    mean       {_fmt(statistics.fmean(views)):>10}",
        f"    min        {_fmt(min(views)):>10}",
        f"    max        {_fmt(max(views)):>10}",
    ]

    # -- By current tier ----------------------------------------------------
    lines += ["", "  VIEWS BY CURRENT DIFFICULTY", ""]
    lines.append(f"    {'tier':<12}{'n':>4}{'median':>10}{'mean':>10}{'min':>10}{'max':>10}")
    for tier in TIER_ORDER:
        tier_rows = [row for row in report.rows if row.current_difficulty is tier]
        if not tier_rows:
            continue
        tier_views = [row.views for row in tier_rows]
        lines.append(
            f"    {tier.value:<12}{len(tier_rows):>4}"
            f"{_fmt(statistics.median(tier_views)):>10}"
            f"{_fmt(statistics.fmean(tier_views)):>10}"
            f"{_fmt(min(tier_views)):>10}{_fmt(max(tier_views)):>10}"
        )

    # -- Extremes -----------------------------------------------------------
    lines += ["", "  TOP 10 MOST VIEWED", ""]
    for row in report.rows[:10]:
        lines.append(
            f"    {_fmt(row.views):>8}  score {row.score:>5.1f}  "
            f"{row.current_difficulty.value if row.current_difficulty else '-':<11}"
            f"{row.title[:30]} - {row.artist[:20]}"
        )

    lines += ["", "  BOTTOM 10 LEAST VIEWED", ""]
    for row in report.rows[-10:]:
        lines.append(
            f"    {_fmt(row.views):>8}  score {row.score:>5.1f}  "
            f"{row.current_difficulty.value if row.current_difficulty else '-':<11}"
            f"{row.title[:30]} - {row.artist[:20]}"
        )

    # -- Disagreements ------------------------------------------------------
    disagreements = sorted(
        (row for row in report.rows if abs(row.distance) >= 2),
        key=lambda row: -abs(row.distance),
    )
    lines += ["", f"  STRONGEST DISAGREEMENTS ({len(disagreements)} songs move 2+ tiers)", ""]
    for row in disagreements[:15]:
        direction = "harder" if row.distance > 0 else "easier"
        lines.append(
            f"    {row.current_difficulty.value:<11} -> {row.proposed_difficulty.value:<11}"
            f"{direction:<8}{_fmt(row.views):>8}  {row.title[:28]} - {row.artist[:18]}"
        )

    # -- Threshold effect ---------------------------------------------------
    lines += ["", "  PROVISIONAL THRESHOLDS", ""]
    lines.append(f"    {'tier':<12}{'score':<14}{'views from':>12}{'songs':>8}")
    for tier, (low, high) in PROVISIONAL_THRESHOLDS.items():
        count = sum(1 for row in report.rows if row.proposed_difficulty is tier)
        lines.append(
            f"    {tier.value:<12}{f'{low:.0f} to {high:.0f}':<14}"
            f"{_fmt(views_for_score(low)):>12}{count:>8}"
        )

    moved = [row for row in report.rows if row.moved]
    lines += [
        "",
        f"  {len(moved)} of {len(report.rows)} songs would change tier "
        f"({len(moved) / len(report.rows) * 100:.0f}%)",
    ]

    # -- Current against proposed, side by side -----------------------------
    lines += ["", "  TIER SIZES", ""]
    lines.append(f"    {'tier':<12}{'current':>9}{'proposed':>10}")
    for tier in TIER_ORDER:
        current = sum(1 for row in report.rows if row.current_difficulty is tier)
        proposed = sum(1 for row in report.rows if row.proposed_difficulty is tier)
        lines.append(f"    {tier.value:<12}{current:>9}{proposed:>10}")

    return "\n".join(lines) + "\n"
