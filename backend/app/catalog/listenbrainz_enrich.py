"""Collect ListenBrainz popularity signals for catalog songs.

Cheap and exact. Lookups are keyed on the MusicBrainz recording id the catalog
already stores, so there is no search, no matching, and no matching risk. That
also means coverage is bounded by MusicBrainz coverage: a song without an MBID
cannot be looked up at all.

Both metrics are stored separately and neither is combined with YouTube here.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.youtube_enrich import record_signal
from app.core.errors import AudidleError
from app.database.catalog_models import ExternalIdentifierRow
from app.models.song import ID_TYPE_RECORDING_MBID, PROVIDER_MUSICBRAINZ
from app.providers.listenbrainz.listenbrainz_provider import ListenBrainzProvider

logger = logging.getLogger(__name__)

PROVIDER_LISTENBRAINZ = "listenbrainz"
METRIC_LISTEN_COUNT = "listen_count"
METRIC_LISTENER_COUNT = "unique_listener_count"


@dataclass
class ListenBrainzReport:
    songs_with_mbid: int = 0
    answered: int = 0
    with_listeners: int = 0
    with_listens: int = 0
    no_data: int = 0
    failed: int = 0
    missing_mbid: int = 0
    notes: list[str] = field(default_factory=list)


async def collect_listenbrainz_signals(
    session: AsyncSession, provider: ListenBrainzProvider, total_songs: int | None = None
) -> ListenBrainzReport:
    """Fetch and store listen and listener counts for every song with an MBID."""
    report = ListenBrainzReport()

    rows = (
        await session.execute(
            select(ExternalIdentifierRow.song_id, ExternalIdentifierRow.identifier).where(
                ExternalIdentifierRow.provider == PROVIDER_MUSICBRAINZ,
                ExternalIdentifierRow.identifier_type == ID_TYPE_RECORDING_MBID,
            )
        )
    ).all()

    report.songs_with_mbid = len(rows)
    if total_songs is not None:
        report.missing_mbid = max(0, total_songs - len(rows))

    if not rows:
        return report

    song_by_mbid = {mbid: song_id for song_id, mbid in rows}

    try:
        found = await provider.get_recording_popularity(list(song_by_mbid))
    except AudidleError as error:
        report.failed = len(rows)
        logger.warning("ListenBrainz lookup failed: %s", error)
        return report

    for mbid, song_id in song_by_mbid.items():
        popularity = found.get(mbid)

        if popularity is None or not popularity.has_data:
            # No data is not zero popularity. Nothing is stored, so the scoring
            # layer sees a missing signal rather than a bottom ranked song.
            report.no_data += 1
            continue

        report.answered += 1

        if popularity.listener_count is not None:
            await record_signal(
                session,
                song_id,
                PROVIDER_LISTENBRAINZ,
                METRIC_LISTENER_COUNT,
                popularity.listener_count,
                source_reference=mbid,
            )
            report.with_listeners += 1

        if popularity.listen_count is not None:
            await record_signal(
                session,
                song_id,
                PROVIDER_LISTENBRAINZ,
                METRIC_LISTEN_COUNT,
                popularity.listen_count,
                source_reference=mbid,
            )
            report.with_listens += 1

    await session.commit()
    return report
