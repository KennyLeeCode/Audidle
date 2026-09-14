"""Read-only diagnostic for YouTube candidate selection.

Answers "why did this song get that video" without touching anything. It stores
nothing, invalidates nothing, and changes no scores. The only side effect is the
quota cost of one search.

It deliberately owns no matching rules. Validation, ranking, and authority all
come from the real enrichment path, so what this prints is what enrichment would
actually do. Duplicating that logic here would let the diagnostic drift from the
behaviour it is supposed to explain, which is the one thing a debugging tool
must never do.
"""

from dataclasses import dataclass, field

from app.catalog.youtube_matcher import (
    TOPIC_CHANNEL,
    VEVO,
    VideoMatch,
    authority_rank,
    has_canonical_evidence,
    pick_best_video,
    score_candidate,
)
from app.providers.youtube.youtube_provider import YouTubePopularityProvider, YouTubeVideo


def upload_kind(match: VideoMatch) -> str:
    """How authoritative the upload is, in words.

    Presentation only. The ranking uses authority_rank, which does not care
    whether an official channel is a Vevo one.
    """
    channel = match.video.channel_title.lower()
    if channel.endswith(TOPIC_CHANNEL):
        return "Topic"
    if VEVO in channel:
        return "VEVO"
    if authority_rank(match) >= 2:
        return "official artist"
    return "other"


@dataclass
class CandidateLine:
    """One evaluated candidate."""

    video_id: str
    title: str
    channel: str
    view_count: int | None
    duration_ms: int | None
    valid: bool
    reason: str
    authority: int
    kind: str
    confidence: str

    @property
    def is_selected_kind(self) -> bool:
        return self.valid


@dataclass
class MatchReport:
    song_title: str
    song_artist: str
    song_duration_ms: int | None
    candidates: list[CandidateLine] = field(default_factory=list)
    selected: CandidateLine | None = None
    selection_reason: str = ""
    unresolved_reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.selected is not None

    @property
    def valid_candidates(self) -> list[CandidateLine]:
        return [line for line in self.candidates if line.valid]


def build_match_report(
    song_title: str,
    song_artist: str,
    song_duration_ms: int | None,
    candidates: list[YouTubeVideo],
) -> MatchReport:
    """Evaluate candidates exactly as enrichment would, and explain the outcome."""
    report = MatchReport(
        song_title=song_title, song_artist=song_artist, song_duration_ms=song_duration_ms
    )

    scored: list[tuple[YouTubeVideo, VideoMatch]] = [
        (video, score_candidate(video, song_title, song_artist, song_duration_ms))
        for video in candidates
    ]

    for video, match in scored:
        report.candidates.append(
            CandidateLine(
                video_id=video.video_id,
                title=video.title,
                channel=video.channel_title,
                view_count=video.view_count,
                duration_ms=video.duration_ms,
                valid=match.accepted,
                reason=match.reason,
                authority=authority_rank(match) if match.accepted else -1,
                kind=upload_kind(match) if match.accepted else "-",
                confidence=match.confidence.value,
            )
        )

    # The real selection, not a reimplementation of it.
    best = pick_best_video(candidates, song_title, song_artist, song_duration_ms)

    if best is None:
        valid = report.valid_candidates
        if not candidates:
            report.unresolved_reason = "the search returned no candidates at all"
        elif not valid:
            report.unresolved_reason = (
                "no candidate passed identity validation, so nothing was forced"
            )
        else:
            report.unresolved_reason = (
                f"{len(valid)} candidates were valid but too close to separate "
                f"confidently, so the song was left for review"
            )
        return report

    report.selected = next(
        line for line in report.candidates if line.video_id == best.video.video_id
    )

    rivals = [
        line
        for line in report.valid_candidates
        if line.video_id != best.video.video_id
    ]
    if not rivals:
        report.selection_reason = "it was the only candidate that passed identity validation"
    else:
        best_views = best.video.view_count or 0
        top_rival = max(rivals, key=lambda line: line.view_count or 0)
        rival_views = top_rival.view_count or 0

        if report.selected.authority > top_rival.authority:
            report.selection_reason = (
                f"it is a more authoritative upload ({report.selected.kind}) than the "
                f"best valid alternative ({top_rival.kind})"
            )
        elif report.selected.authority < top_rival.authority:
            # Won despite lower authority, which only happens on a dominant
            # audience. Saying which bar it cleared is the useful part.
            multiple = best_views / max(rival_views, 1)
            bar = "the canonical" if has_canonical_evidence(best) else "the full"
            report.selection_reason = (
                f"it is less authoritative ({report.selected.kind}) than the best "
                f"alternative ({top_rival.kind}), but carries {multiple:.0f}x the "
                f"audience ({best_views:,} against {rival_views:,}), clearing "
                f"{bar} dominance bar"
            )
        else:
            report.selection_reason = (
                f"equally authoritative alternatives existed, and this upload has the "
                f"largest audience ({best_views:,} against {rival_views:,})"
            )

    return report


async def fetch_candidates(
    provider: YouTubePopularityProvider, title: str, artist: str
) -> list[YouTubeVideo]:
    """One search plus one batched hydration, the same as enrichment does."""
    candidates = await provider.search_song_video(title, artist)
    if not candidates:
        return []

    hydrated = await provider.hydrate_videos([video.video_id for video in candidates])
    return [hydrated.get(video.video_id, video) for video in candidates]


def _views(value: int | None) -> str:
    return "hidden" if value is None else f"{value:,}"


def render_match_report(report: MatchReport) -> str:
    duration = (report.song_duration_ms or 0) / 1000
    lines = [
        "",
        f"MATCH REPORT  {report.song_title} - {report.song_artist}",
        f"  catalog recording is {duration:.0f}s",
        "",
        f"  {len(report.candidates)} candidates, {len(report.valid_candidates)} valid",
        "",
    ]

    for line in report.candidates:
        verdict = "VALID   " if line.valid else "rejected"
        seconds = (line.duration_ms or 0) / 1000
        is_selected = report.selected and line.video_id == report.selected.video_id
        marker = " <- selected" if is_selected else ""
        lines.append(
            f"  {verdict} {line.video_id:<12} {_views(line.view_count):>15}  "
            f"{seconds:>6.0f}s  {line.kind:<16}{line.channel[:22]:24}{marker}"
        )
        lines.append(f"           {line.title[:66]}")
        label = "passed" if line.valid else "rejected"
        lines.append(f"           {label}: {line.reason}")
        if line.valid:
            lines.append(f"           confidence: {line.confidence}")
        lines.append("")

    if not report.resolved:
        lines += ["  UNRESOLVED", f"    {report.unresolved_reason}", ""]
        return "\n".join(lines) + "\n"

    selected = report.selected
    lines += [
        "  SELECTED",
        f"    video       {selected.video_id}",
        f"    title       {selected.title}",
        f"    channel     {selected.channel}",
        f"    upload kind {selected.kind}",
        f"    views       {_views(selected.view_count)}",
        f"    duration    {(selected.duration_ms or 0) / 1000:.0f}s",
        f"    confidence  {selected.confidence}",
        "",
        f"    passed identity validation because: {selected.reason}",
        f"    chosen because: {report.selection_reason}",
        "",
    ]

    return "\n".join(lines) + "\n"
