"""The second calibration report: YouTube against ListenBrainz.

Compares the two signals, examines the cases where YouTube is known to be
biased, and evaluates several candidate blends without adopting any of them.

Two rules shape the whole module:

**Missing is not zero.** A song with no YouTube match or no ListenBrainz data
has an unknown score for that source, held as None. Treating it as zero would
rank Frank Ocean as obscure purely because he does not upload to YouTube, which
is exactly the bias this second signal exists to correct.

**No model is applied.** Every blend here is an experiment printed for
inspection. Difficulty assignment is untouched.
"""

import statistics
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.popularity import (
    score_from_listen_count,
    score_from_listener_count,
    score_from_youtube_views,
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
class SignalRow:
    """Every signal known for one song. None means unknown, never zero."""

    song_id: str
    title: str
    artist: str
    current_difficulty: Difficulty | None

    youtube_views: int | None = None
    youtube_confidence: str | None = None
    listeners: int | None = None
    listens: int | None = None

    @property
    def youtube_score(self) -> float | None:
        if self.youtube_views is None:
            return None
        return score_from_youtube_views(self.youtube_views)

    @property
    def listener_score(self) -> float | None:
        return None if self.listeners is None else score_from_listener_count(self.listeners)

    @property
    def listen_score(self) -> float | None:
        return None if self.listens is None else score_from_listen_count(self.listens)

    @property
    def label(self) -> str:
        return f"{self.title} - {self.artist}"


@dataclass
class MultiSignalReport:
    rows: list[SignalRow] = field(default_factory=list)
    total_songs: int = 0

    @property
    def with_youtube(self) -> list[SignalRow]:
        return [row for row in self.rows if row.youtube_views is not None]

    @property
    def with_listeners(self) -> list[SignalRow]:
        return [row for row in self.rows if row.listeners is not None]

    @property
    def with_both(self) -> list[SignalRow]:
        return [
            row
            for row in self.rows
            if row.youtube_views is not None and row.listeners is not None
        ]


async def build_multisignal_report(session: AsyncSession) -> MultiSignalReport:
    """Gather every stored popularity signal alongside the current difficulty."""
    report = MultiSignalReport()

    songs = {song.id: song for song in (await session.execute(select(CatalogSong))).scalars()}
    report.total_songs = len(songs)

    confidence = dict(
        (
            await session.execute(
                select(
                    ExternalIdentifierRow.song_id, ExternalIdentifierRow.confidence
                ).where(
                    ExternalIdentifierRow.provider == "youtube",
                    ExternalIdentifierRow.identifier_type == "video_id",
                )
            )
        ).all()
    )

    signals: dict[str, dict[tuple[str, str], int]] = {}
    for signal in (await session.execute(select(SongPopularitySignal))).scalars():
        signals.setdefault(signal.song_id, {})[(signal.source, signal.metric)] = signal.value

    for song_id, song in songs.items():
        eligibility = await session.get(SongEligibility, song_id)
        values = signals.get(song_id, {})

        report.rows.append(
            SignalRow(
                song_id=song_id,
                title=song.title,
                artist=song.artist_credit,
                current_difficulty=(
                    Difficulty(eligibility.difficulty)
                    if eligibility and eligibility.difficulty
                    else None
                ),
                youtube_views=values.get(("youtube", "view_count")),
                youtube_confidence=confidence.get(song_id),
                listeners=values.get(("listenbrainz", "unique_listener_count")),
                listens=values.get(("listenbrainz", "listen_count")),
            )
        )

    return report


# -- Candidate models --------------------------------------------------------
#
# Experiments, not settings. Every one handles a missing signal by reweighting
# over what is present rather than substituting zero.


def model_youtube_only(row: SignalRow) -> float | None:
    return row.youtube_score


def model_listeners_only(row: SignalRow) -> float | None:
    return row.listener_score


def _blend(row: SignalRow, youtube_weight: float) -> float | None:
    """Weighted blend that renormalizes when a signal is missing."""
    parts: list[tuple[float, float]] = []
    if row.youtube_score is not None:
        parts.append((row.youtube_score, youtube_weight))
    if row.listener_score is not None:
        parts.append((row.listener_score, 1.0 - youtube_weight))

    if not parts:
        return None

    total_weight = sum(weight for _, weight in parts)
    if total_weight == 0:
        return None
    return round(sum(score * weight for score, weight in parts) / total_weight, 2)


def model_youtube_dominant(row: SignalRow) -> float | None:
    return _blend(row, 0.7)


def model_balanced(row: SignalRow) -> float | None:
    return _blend(row, 0.5)


def model_adaptive(row: SignalRow) -> float | None:
    """Balanced when both signals exist, and the survivor alone when one does not.

    Identical arithmetic to the balanced blend. It is listed separately because
    the interesting question is not the weight, it is what happens to a song
    with only one signal, and this makes that behaviour explicit.
    """
    return _blend(row, 0.5)


MODELS = {
    "A youtube only": model_youtube_only,
    "B listeners only": model_listeners_only,
    "C youtube 0.7": model_youtube_dominant,
    "D balanced 0.5": model_balanced,
    "E adaptive": model_adaptive,
}


# -- Statistics --------------------------------------------------------------


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Rank correlation, so the very different scales do not matter.

    Pearson on raw counts would be meaningless here: YouTube views run to
    billions and ListenBrainz listeners to hundreds of thousands. What matters
    is whether the two sources order songs the same way.
    """
    if len(pairs) < 3:
        return None

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda index: values[index])
        result = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position
            while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
                end += 1
            average = (position + end) / 2 + 1
            for index in range(position, end + 1):
                result[order[index]] = average
            position = end + 1
        return result

    left = ranks([pair[0] for pair in pairs])
    right = ranks([pair[1] for pair in pairs])

    mean_left = statistics.fmean(left)
    mean_right = statistics.fmean(right)
    covariance = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True)
    )
    spread = (
        sum((a - mean_left) ** 2 for a in left) * sum((b - mean_right) ** 2 for b in right)
    ) ** 0.5

    return round(covariance / spread, 3) if spread else None


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= limit:
            return f"{value / limit:.1f}{suffix}"
    return f"{int(value)}"


def _score(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


# -- Rendering ---------------------------------------------------------------


# The songs the first calibration flagged as YouTube being wrong about, plus the
# ones YouTube could not match at all. These are the reason ListenBrainz was
# added, so they get their own table.
PROBLEM_CASES = (
    "Redbone",
    "One Dance",
    "Lucid Dreams",
    "Bags",
    "Chamber Of Reflection",
    "Shrike",
    "Two Slow Dancers",
    "Nights",
    "Ivy",
    "Pink + White",
    "Self Control",
    "Moon River",
    "Runaway",
    "Fade Into You",
)


def _is_problem_case(row: SignalRow) -> bool:
    title = row.title.lower()
    return any(case.lower() in title for case in PROBLEM_CASES)


def render_multisignal_report(report: MultiSignalReport) -> str:
    lines: list[str] = ["", "SECOND CALIBRATION: YOUTUBE AND LISTENBRAINZ", ""]

    rows = report.rows
    with_youtube = report.with_youtube
    with_listeners = report.with_listeners
    with_both = report.with_both
    neither = [
        row for row in rows if row.youtube_views is None and row.listeners is None
    ]

    # -- Coverage ------------------------------------------------------------
    lines += [
        "  COVERAGE",
        "",
        f"    songs in catalog          {report.total_songs:>5}",
        f"    YouTube view count        {len(with_youtube):>5}",
        f"    LB unique listeners       {len(with_listeners):>5}",
        f"    LB listen count           "
        f"{len([r for r in rows if r.listens is not None]):>5}",
        f"    both signals              {len(with_both):>5}",
        f"    YouTube only              "
        f"{len([r for r in with_youtube if r.listeners is None]):>5}",
        f"    ListenBrainz only         "
        f"{len([r for r in with_listeners if r.youtube_views is None]):>5}",
        f"    neither (unknown, not 0)  {len(neither):>5}",
    ]

    if not with_listeners:
        lines.append("")
        lines.append("  No ListenBrainz data yet. Run enrich-listenbrainz first.")
        return "\n".join(lines) + "\n"

    # -- Distribution --------------------------------------------------------
    listeners = [row.listeners for row in with_listeners]
    listens = [row.listens for row in rows if row.listens is not None]
    lines += [
        "",
        "  LISTENBRAINZ DISTRIBUTION",
        "",
        f"    unique listeners   median {_fmt(statistics.median(listeners)):>8}"
        f"   min {_fmt(min(listeners)):>8}   max {_fmt(max(listeners)):>8}",
        f"    listens            median {_fmt(statistics.median(listens)):>8}"
        f"   min {_fmt(min(listens)):>8}   max {_fmt(max(listens)):>8}",
    ]

    # -- By current tier -----------------------------------------------------
    lines += ["", "  BY CURRENT DIFFICULTY", ""]
    lines.append(
        f"    {'tier':<12}{'n':>3}{'median views':>14}{'median listeners':>18}"
        f"{'median listens':>16}"
    )
    for tier in TIER_ORDER:
        tier_rows = [row for row in rows if row.current_difficulty is tier]
        if not tier_rows:
            continue
        tier_views = [r.youtube_views for r in tier_rows if r.youtube_views is not None]
        tier_listeners = [r.listeners for r in tier_rows if r.listeners is not None]
        tier_listens = [r.listens for r in tier_rows if r.listens is not None]
        lines.append(
            f"    {tier.value:<12}{len(tier_rows):>3}"
            f"{(_fmt(statistics.median(tier_views)) if tier_views else '-'):>14}"
            f"{(_fmt(statistics.median(tier_listeners)) if tier_listeners else '-'):>18}"
            f"{(_fmt(statistics.median(tier_listens)) if tier_listens else '-'):>16}"
        )

    # -- Extremes ------------------------------------------------------------
    ranked = sorted(with_listeners, key=lambda row: -row.listeners)
    lines += ["", "  TOP 10 BY UNIQUE LISTENERS", ""]
    for row in ranked[:10]:
        lines.append(
            f"    {_fmt(row.listeners):>8} listeners  yt {_fmt(row.youtube_views):>7}  "
            f"{(row.current_difficulty.value if row.current_difficulty else '-'):<11}"
            f"{row.label[:44]}"
        )
    lines += ["", "  BOTTOM 10 BY UNIQUE LISTENERS", ""]
    for row in ranked[-10:]:
        lines.append(
            f"    {_fmt(row.listeners):>8} listeners  yt {_fmt(row.youtube_views):>7}  "
            f"{(row.current_difficulty.value if row.current_difficulty else '-'):<11}"
            f"{row.label[:44]}"
        )

    # -- Correlation ---------------------------------------------------------
    pairs = [(float(row.youtube_views), float(row.listeners)) for row in with_both]
    rho = spearman(pairs)
    lines += [
        "",
        "  RANK CORRELATION (Spearman, on the songs with both signals)",
        "",
        f"    YouTube views against LB unique listeners   rho = {rho}   n = {len(pairs)}",
    ]
    listen_pairs = [
        (float(row.youtube_views), float(row.listens))
        for row in rows
        if row.youtube_views is not None and row.listens is not None
    ]
    lines.append(
        f"    YouTube views against LB listens            "
        f"rho = {spearman(listen_pairs)}   n = {len(listen_pairs)}"
    )

    # -- Agreement and disagreement ------------------------------------------
    gaps = [
        (abs(row.youtube_score - row.listener_score), row) for row in with_both
    ]
    gaps.sort(key=lambda pair: pair[0])

    lines += ["", "  STRONGEST AGREEMENT (scores within a point or two)", ""]
    for gap, row in gaps[:8]:
        lines.append(
            f"    gap {gap:>5.1f}   yt {_score(row.youtube_score):>5}  "
            f"lb {_score(row.listener_score):>5}   {row.label[:44]}"
        )

    lines += ["", "  STRONGEST DISAGREEMENT", ""]
    for gap, row in reversed(gaps[-12:]):
        direction = "LB higher" if row.listener_score > row.youtube_score else "YT higher"
        lines.append(
            f"    gap {gap:>5.1f}   yt {_score(row.youtube_score):>5}  "
            f"lb {_score(row.listener_score):>5}  {direction:<10}{row.label[:38]}"
        )

    # -- The problem cases ---------------------------------------------------
    lines += [
        "",
        "  KNOWN YOUTUBE PROBLEM CASES",
        "",
        f"    {'song':<34}{'current':<11}{'views':>8}{'yt':>6}"
        f"{'listeners':>11}{'lb':>6}{'listens':>10}  agreement",
    ]
    for row in rows:
        if not _is_problem_case(row):
            continue
        if row.youtube_score is None or row.listener_score is None:
            agreement = "one signal missing"
        else:
            gap = abs(row.youtube_score - row.listener_score)
            agreement = "agree" if gap < 10 else ("close" if gap < 20 else "DISAGREE")
        lines.append(
            f"    {row.label[:33]:<34}"
            f"{(row.current_difficulty.value if row.current_difficulty else '-'):<11}"
            f"{_fmt(row.youtube_views):>8}{_score(row.youtube_score):>6}"
            f"{_fmt(row.listeners):>11}{_score(row.listener_score):>6}"
            f"{_fmt(row.listens):>10}  {agreement}"
        )

    # -- Models --------------------------------------------------------------
    lines += ["", "  CANDIDATE MODELS, scored for every song", ""]
    lines.append(f"    {'model':<20}{'scored':>8}{'median':>9}{'unscored':>10}")
    for name, model in MODELS.items():
        scores = [model(row) for row in rows]
        scored = [value for value in scores if value is not None]
        lines.append(
            f"    {name:<20}{len(scored):>8}{statistics.median(scored):>9.1f}"
            f"{len(rows) - len(scored):>10}"
        )

    lines += ["", "  THE PROBLEM CASES UNDER EACH MODEL", ""]
    header = "    " + f"{'song':<30}" + "".join(f"{name.split()[0]:>7}" for name in MODELS)
    lines.append(header)
    for row in rows:
        if not _is_problem_case(row):
            continue
        cells = "".join(f"{_score(model(row)):>7}" for model in MODELS.values())
        lines.append(f"    {row.label[:29]:<30}{cells}")

    return "\n".join(lines) + "\n"
