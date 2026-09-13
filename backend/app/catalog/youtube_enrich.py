"""YouTube matching and view count collection.

Two passes, split by cost rather than by convenience.

`match_songs_to_videos` is the expensive one. It searches for songs that have no
video yet, at 100 quota units each, and stops cleanly when the budget runs out.
It is resumable: a song with a stored video id is skipped without spending
anything, so stopping after 40 of 89 and rerunning tomorrow continues at 41.

`refresh_view_counts` is the cheap one. It re-reads counts for videos already
matched, fifty at a time for a single unit, so refreshing the whole catalog
costs almost nothing and can run as often as you like.

The video id is stored as an external identifier, reusing the same uniqueness
and deduplication the rest of the catalog gets. The richer match details go into
song_provider_metadata, and the raw view count into song_popularity_signals.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.writer import attach_identifier, record_unresolved, store_provider_metadata
from app.catalog.youtube_matcher import (
    VideoMatchConfidence,
    best_rejection_reason,
    pick_best_video,
)
from app.core.errors import AudidleError
from app.database.catalog_models import (
    CatalogSong,
    ExternalIdentifierRow,
    SongPopularitySignal,
)
from app.providers.youtube.youtube_provider import (
    COST_SEARCH,
    QuotaExhaustedError,
    YouTubePopularityProvider,
)

logger = logging.getLogger(__name__)

PROVIDER_YOUTUBE = "youtube"
ID_TYPE_VIDEO = "video_id"
METRIC_VIEW_COUNT = "view_count"


@dataclass
class YouTubeMatchReport:
    considered: int = 0
    skipped_already_matched: int = 0
    verified_official: int = 0
    high_confidence: int = 0
    unresolved: int = 0
    failed: int = 0
    view_counts_stored: int = 0
    quota_used: int = 0
    quota_exhausted: bool = False
    unresolved_reasons: list[str] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return self.verified_official + self.high_confidence


async def _video_id_for(session: AsyncSession, song_id: str) -> str | None:
    statement = select(ExternalIdentifierRow.identifier).where(
        ExternalIdentifierRow.song_id == song_id,
        ExternalIdentifierRow.provider == PROVIDER_YOUTUBE,
        ExternalIdentifierRow.identifier_type == ID_TYPE_VIDEO,
    )
    return (await session.execute(statement)).scalars().first()


async def record_signal(
    session: AsyncSession,
    song_id: str,
    source: str,
    metric: str,
    value: int,
    source_reference: str | None = None,
) -> None:
    """Store a raw popularity measurement.

    Upserted on (song, source, metric) so a refresh updates rather than
    appending. The raw number is never replaced by a derived score, so the
    scoring formula can change later without re-collecting anything.
    """
    from datetime import UTC, datetime

    existing = (
        await session.execute(
            select(SongPopularitySignal).where(
                SongPopularitySignal.song_id == song_id,
                SongPopularitySignal.source == source,
                SongPopularitySignal.metric == metric,
            )
        )
    ).scalars().first()

    row = existing or SongPopularitySignal(song_id=song_id, source=source, metric=metric)
    row.value = value
    row.source_reference = source_reference
    row.measured_at = datetime.now(UTC)

    if existing is None:
        session.add(row)
    await session.flush()


async def match_songs_to_videos(
    session: AsyncSession,
    provider: YouTubePopularityProvider,
    limit: int | None = None,
    progress=None,
    only_title: str | None = None,
) -> YouTubeMatchReport:
    """Find and store a YouTube video for each unmatched song.

    `only_title` narrows the run to songs whose title contains that text. At 100
    quota units per search, walking the whole catalog to reach one song spends
    the day's budget on songs that were already known to fail.

    The expensive pass. Every song costs 100 quota units for the search plus one
    shared unit for hydrating the shortlist, so the default daily quota covers
    roughly ninety songs.
    """
    report = YouTubeMatchReport()

    songs = (await session.execute(select(CatalogSong))).scalars().all()
    if only_title:
        needle = only_title.lower()
        songs = [song for song in songs if needle in song.title.lower()]
    if limit:
        songs = songs[:limit]

    for index, song in enumerate(songs, start=1):
        report.considered += 1

        # Resumability. Costs nothing and is the reason a stopped run can be
        # restarted the next day.
        if await _video_id_for(session, song.id):
            report.skipped_already_matched += 1
            continue

        # Stop before overspending rather than failing part way through a song.
        if not provider.can_afford(COST_SEARCH + 1):
            report.quota_exhausted = True
            logger.warning(
                "quota budget reached after %s songs, rerun to continue", index - 1
            )
            break

        try:
            candidates = await provider.search_song_video(song.title, song.artist_credit)

            # Search results carry no duration or view count, so the shortlist
            # is hydrated in one batched call before scoring.
            if candidates:
                hydrated = await provider.hydrate_videos(
                    [video.video_id for video in candidates]
                )
                candidates = [
                    hydrated.get(video.video_id, video) for video in candidates
                ]

            match = pick_best_video(
                candidates, song.title, song.artist_credit, song.duration_ms
            )

            if match is None:
                reason = best_rejection_reason(
                    candidates, song.title, song.artist_credit, song.duration_ms
                )
                report.unresolved += 1
                report.unresolved_reasons.append(f"{song.title} - {song.artist_credit}: {reason}")
                await record_unresolved(
                    session,
                    PROVIDER_YOUTUBE,
                    f"{song.title} - {song.artist_credit}: {reason}",
                    {"title": song.title, "artist": song.artist_credit},
                    song,
                )
                await session.commit()
                continue

            video = match.video

            await attach_identifier(
                session,
                song,
                PROVIDER_YOUTUBE,
                ID_TYPE_VIDEO,
                video.video_id,
                confidence=match.confidence.value,
            )
            await store_provider_metadata(
                session,
                song,
                PROVIDER_YOUTUBE,
                video.video_id,
                {
                    "video_id": video.video_id,
                    "video_title": video.title,
                    "channel_id": video.channel_id,
                    "channel_title": video.channel_title,
                    "duration_ms": video.duration_ms,
                    "match_confidence": match.confidence.value,
                    "match_method": match.method,
                    "match_score": match.score,
                    "match_reason": match.reason,
                },
            )

            if video.view_count is not None:
                await record_signal(
                    session,
                    song.id,
                    PROVIDER_YOUTUBE,
                    METRIC_VIEW_COUNT,
                    video.view_count,
                    source_reference=video.video_id,
                )
                report.view_counts_stored += 1

            if match.confidence is VideoMatchConfidence.VERIFIED_OFFICIAL:
                report.verified_official += 1
            else:
                report.high_confidence += 1

            await session.commit()

        except QuotaExhaustedError:
            report.quota_exhausted = True
            logger.warning("YouTube quota exhausted, stopping cleanly")
            break
        except AudidleError as error:
            report.failed += 1
            logger.warning("YouTube matching failed for %s: %s", song.title, error)
        except Exception:
            report.failed += 1
            logger.exception("unexpected error matching %s", song.title)

        if progress:
            progress(index, len(songs), song.title)

    report.quota_used = provider.quota_used
    return report


async def refresh_view_counts(
    session: AsyncSession, provider: YouTubePopularityProvider
) -> YouTubeMatchReport:
    """Re-read view counts for every song that already has a video.

    The cheap pass, at one quota unit per fifty songs.
    """
    report = YouTubeMatchReport()

    rows = (
        await session.execute(
            select(ExternalIdentifierRow.song_id, ExternalIdentifierRow.identifier).where(
                ExternalIdentifierRow.provider == PROVIDER_YOUTUBE,
                ExternalIdentifierRow.identifier_type == ID_TYPE_VIDEO,
            )
        )
    ).all()

    report.considered = len(rows)
    if not rows:
        return report

    by_video = {video_id: song_id for song_id, video_id in rows}

    try:
        counts = await provider.refresh_video_stats(list(by_video))
    except AudidleError as error:
        report.failed = len(rows)
        logger.warning("could not refresh YouTube stats: %s", error)
        report.quota_used = provider.quota_used
        return report

    for video_id, views in counts.items():
        await record_signal(
            session,
            by_video[video_id],
            PROVIDER_YOUTUBE,
            METRIC_VIEW_COUNT,
            views,
            source_reference=video_id,
        )
        report.view_counts_stored += 1

    await session.commit()
    report.quota_used = provider.quota_used
    return report
