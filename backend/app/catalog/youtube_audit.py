"""Auditing and invalidating YouTube matches.

Re-scores every stored match against the current matcher rules. A match that no
longer passes is not silently replaced: it is written into unresolved_matches
with its full previous state, then removed, so the next enrichment run treats
the song as unmatched and searches again from scratch.

On preserving history. The signals table is unique on (song_id, source, metric),
so it holds one current value per metric and cannot keep a time series. The old
view count is therefore preserved inside the unresolved_matches payload rather
than in the signals table. That is a real schema limitation, and if measurement
history becomes important the unique constraint is the thing to relax.
"""

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.youtube_matcher import score_candidate
from app.database.catalog_models import (
    CatalogSong,
    ExternalIdentifierRow,
    SongPopularitySignal,
    SongProviderMetadata,
    UnresolvedMatch,
)
from app.providers.youtube.youtube_provider import YouTubeVideo

logger = logging.getLogger(__name__)

PROVIDER_YOUTUBE = "youtube"
ID_TYPE_VIDEO = "video_id"


@dataclass
class AuditFinding:
    title: str
    artist: str
    video_id: str
    video_title: str
    channel: str
    views: int | None
    duration_ms: int | None
    song_duration_ms: int | None
    stored_confidence: str
    stored_reason: str
    verdict: str
    now_accepted: bool

    @property
    def is_wrong(self) -> bool:
        return not self.now_accepted


@dataclass
class AuditReport:
    checked: int = 0
    still_valid: int = 0
    invalidated: int = 0
    findings: list[AuditFinding] = field(default_factory=list)


async def audit_youtube_matches(
    session: AsyncSession, invalidate: bool = False
) -> AuditReport:
    """Re-score every stored YouTube match against today's rules.

    Uses only stored metadata, so it costs no API quota. When `invalidate` is
    set, matches that no longer pass are recorded and removed.
    """
    report = AuditReport()

    rows = (
        await session.execute(
            select(CatalogSong, ExternalIdentifierRow)
            .join(
                ExternalIdentifierRow,
                ExternalIdentifierRow.song_id == CatalogSong.id,
            )
            .where(
                ExternalIdentifierRow.provider == PROVIDER_YOUTUBE,
                ExternalIdentifierRow.identifier_type == ID_TYPE_VIDEO,
            )
        )
    ).all()

    for song, identifier in rows:
        report.checked += 1

        metadata = (
            await session.execute(
                select(SongProviderMetadata).where(
                    SongProviderMetadata.song_id == song.id,
                    SongProviderMetadata.provider == PROVIDER_YOUTUBE,
                )
            )
        ).scalars().first()
        stored = json.loads(metadata.metadata_json) if metadata else {}

        signal = (
            await session.execute(
                select(SongPopularitySignal).where(
                    SongPopularitySignal.song_id == song.id,
                    SongPopularitySignal.source == PROVIDER_YOUTUBE,
                    SongPopularitySignal.metric == "view_count",
                )
            )
        ).scalars().first()

        # Rebuild the candidate from what was stored and re-score it.
        video = YouTubeVideo(
            video_id=identifier.identifier,
            title=stored.get("video_title", ""),
            channel_id=stored.get("channel_id", ""),
            channel_title=stored.get("channel_title", ""),
            duration_ms=stored.get("duration_ms"),
            view_count=signal.value if signal else None,
        )
        rescored = score_candidate(video, song.title, song.artist_credit, song.duration_ms)

        finding = AuditFinding(
            title=song.title,
            artist=song.artist_credit,
            video_id=video.video_id,
            video_title=video.title,
            channel=video.channel_title,
            views=video.view_count,
            duration_ms=video.duration_ms,
            song_duration_ms=song.duration_ms,
            stored_confidence=identifier.confidence,
            stored_reason=stored.get("match_reason", ""),
            verdict=rescored.reason,
            now_accepted=rescored.accepted,
        )
        report.findings.append(finding)

        if finding.now_accepted:
            report.still_valid += 1
            continue

        if not invalidate:
            continue

        # Preserve the whole previous match before removing it.
        session.add(
            UnresolvedMatch(
                song_id=song.id,
                provider=PROVIDER_YOUTUBE,
                reason=(
                    f"invalidated match for {song.title} - {song.artist_credit}: "
                    f"{rescored.reason}"
                ),
                candidate_payload=json.dumps(
                    {
                        "invalidated_video_id": video.video_id,
                        "video_title": video.title,
                        "channel_title": video.channel_title,
                        "previous_confidence": identifier.confidence,
                        "previous_view_count": video.view_count,
                        "previous_match_reason": stored.get("match_reason"),
                        "rejected_because": rescored.reason,
                    },
                    separators=(",", ":"),
                ),
                confidence="invalidated",
                status="pending",
            )
        )

        await session.delete(identifier)
        if metadata:
            await session.delete(metadata)
        if signal:
            await session.delete(signal)

        report.invalidated += 1
        logger.info("invalidated %s: %s", song.title, rescored.reason)

    if invalidate:
        await session.commit()

    return report


def render_audit(report: AuditReport, show_valid: bool = False) -> str:
    lines = ["", "YOUTUBE MATCH AUDIT", ""]
    lines.append(f"  matches checked   {report.checked:>4}")
    lines.append(f"  still valid       {report.still_valid:>4}")
    lines.append(f"  now rejected      {report.checked - report.still_valid:>4}")
    if report.invalidated:
        lines.append(f"  invalidated       {report.invalidated:>4}")

    wrong = [finding for finding in report.findings if finding.is_wrong]
    if wrong:
        lines += ["", "  MATCHES THAT NO LONGER PASS", ""]
        for finding in wrong:
            song_seconds = (finding.song_duration_ms or 0) / 1000
            video_seconds = (finding.duration_ms or 0) / 1000
            lines += [
                f"    {finding.title} - {finding.artist}",
                f"       video     {finding.video_id}  {finding.video_title[:54]}",
                f"       channel   {finding.channel}",
                f"       views     {finding.views:,}" if finding.views else "       views     -",
                f"       duration  song {song_seconds:.0f}s / video {video_seconds:.0f}s",
                f"       was       {finding.stored_confidence}  ({finding.stored_reason})",
                f"       now       rejected: {finding.verdict}",
                "",
            ]

    return "\n".join(lines) + "\n"
