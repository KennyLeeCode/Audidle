"""Catalog admin commands.

    py -m app.catalog migrate-curated   move the old curated table into the catalog
    py -m app.catalog enrich-musicbrainz  attach MusicBrainz identity, ISRCs, tags
    py -m app.catalog enrich-spotify      attach Spotify ids and artwork via ISRC
    py -m app.catalog recompute           recalculate eligibility for every song
    py -m app.catalog enrich-youtube      match songs to videos (expensive, resumable)
    py -m app.catalog refresh-youtube     re-read view counts (cheap)
    py -m app.catalog enrich-listenbrainz collect listen and unique listener counts
    py -m app.catalog popularity-report   YouTube views against current difficulty
    py -m app.catalog signal-report       YouTube and ListenBrainz side by side
    py -m app.catalog audit-youtube       re-score stored matches, no quota cost
    py -m app.catalog audit-youtube --force   also invalidate the ones that fail
    py -m app.catalog model-comparison    candidate scoring models, applied to nothing
    py -m app.catalog stats             counts per tier and per data source
    py -m app.catalog validate          report what is missing and why
    py -m app.catalog unresolved        matches parked for review

More commands are added as the pipeline grows. Everything here is safe to rerun.
"""

import argparse
import asyncio
import logging
import sys

from app.config.settings import get_settings
from app.database.database import (
    build_engine,
    build_session_factory,
    create_schema,
    session_scope,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("catalog")


async def _with_session(handler, *args):
    """Open the database, run a handler, and dispose cleanly."""
    settings = get_settings()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(factory) as session:
            return await handler(session, settings, *args)
    finally:
        await engine.dispose()


# -- Commands ---------------------------------------------------------------


async def _migrate_curated(session, settings, force: bool = False) -> int:
    from app.catalog.migrate_curated import migrate_curated_songs

    report = await migrate_curated_songs(session, settings.audio_dir)

    logger.info("MIGRATION FROM curated_songs\n")
    logger.info(f"  curated rows read      {report.total:>6}")
    logger.info(f"  songs created          {report.created:>6}")
    logger.info(f"  songs updated          {report.updated:>6}")
    logger.info(f"  identifiers attached   {report.identifiers:>6}")
    logger.info(f"  ISRCs attached         {report.isrcs:>6}")
    logger.info(f"  genre links            {report.genres:>6}")
    logger.info(f"  popularity rows        {report.popularity:>6}")
    logger.info(f"  playable audio         {report.audio:>6}")
    logger.info(f"  game eligible          {report.eligible:>6}")
    if report.skipped:
        logger.warning("  skipped: %s", ", ".join(report.skipped))
    return 0


def _progress(done: int, total: int, title: str) -> None:
    logger.info("  [%4d/%d] %s", done, total, title[:48])


async def _enrich_musicbrainz(session, settings, force: bool = False) -> int:
    from app.catalog.enrich import enrich_with_musicbrainz
    from app.dependencies import get_musicbrainz_provider

    provider = get_musicbrainz_provider()
    logger.info(
        "enriching from MusicBrainz at %.1f requests per second. "
        "Safe to interrupt, rerunning resumes where it stopped.",
        settings.musicbrainz_rate_limit,
    )
    try:
        report = await enrich_with_musicbrainz(
            session, provider, progress=_progress, force=force
        )
    finally:
        await provider.aclose()

    _render_enrichment("MUSICBRAINZ", report)
    return 0


async def _enrich_spotify(session, settings, force: bool = False) -> int:
    from app.catalog.enrich import enrich_with_spotify
    from app.dependencies import get_spotify_client
    from app.providers.spotify.spotify_song_provider import SpotifySongProvider

    if not settings.spotify_configured:
        logger.error("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set")
        return 1

    client = get_spotify_client()
    provider = SpotifySongProvider(client=client, market=settings.spotify_market)
    try:
        report = await enrich_with_spotify(
            session, provider, progress=_progress, force=force
        )
    finally:
        await client.aclose()

    _render_enrichment("SPOTIFY", report)
    return 0


async def _enrich_youtube(session, settings, force: bool = False) -> int:
    from app.catalog.youtube_enrich import match_songs_to_videos
    from app.providers.youtube.youtube_provider import YouTubePopularityProvider

    if not settings.youtube_api_key:
        logger.error("YOUTUBE_API_KEY must be set in .env")
        return 1

    provider = YouTubePopularityProvider(
        api_key=settings.youtube_api_key,
        daily_quota=settings.youtube_daily_quota,
        quota_reserve=settings.youtube_quota_reserve,
    )
    logger.info(
        "matching songs to YouTube videos. Each search costs 100 quota units of "
        "%s, so roughly %s songs fit in one day. Safe to interrupt.",
        settings.youtube_daily_quota,
        (settings.youtube_daily_quota - settings.youtube_quota_reserve) // 101,
    )
    try:
        report = await match_songs_to_videos(session, provider, progress=_progress)
    finally:
        await provider.aclose()

    logger.info("YOUTUBE MATCHING\n")
    logger.info(f"  considered            {report.considered:>6}")
    logger.info(f"  already matched       {report.skipped_already_matched:>6}")
    logger.info(f"  verified official     {report.verified_official:>6}")
    logger.info(f"  high confidence       {report.high_confidence:>6}")
    logger.info(f"  unresolved            {report.unresolved:>6}")
    logger.info(f"  failed                {report.failed:>6}")
    logger.info(f"  view counts stored    {report.view_counts_stored:>6}")
    logger.info(f"  quota used            {report.quota_used:>6}")
    if report.quota_exhausted:
        logger.warning("Quota budget reached. Rerun tomorrow to continue.")
    if report.unresolved_reasons:
        logger.info("unresolved:")
        for reason in report.unresolved_reasons[:15]:
            logger.info(f"      {reason[:100]}")
        if len(report.unresolved_reasons) > 15:
            logger.info(f"      ... and {len(report.unresolved_reasons) - 15} more")
    return 0


async def _refresh_youtube(session, settings, force: bool = False) -> int:
    from app.catalog.youtube_enrich import refresh_view_counts
    from app.providers.youtube.youtube_provider import YouTubePopularityProvider

    if not settings.youtube_api_key:
        logger.error("YOUTUBE_API_KEY must be set in .env")
        return 1

    provider = YouTubePopularityProvider(
        api_key=settings.youtube_api_key,
        daily_quota=settings.youtube_daily_quota,
        quota_reserve=settings.youtube_quota_reserve,
    )
    try:
        report = await refresh_view_counts(session, provider)
    finally:
        await provider.aclose()

    logger.info(
        "refreshed %s of %s view counts for %s quota units",
        report.view_counts_stored,
        report.considered,
        report.quota_used,
    )
    return 0


async def _enrich_listenbrainz(session, settings, force: bool = False) -> int:
    from sqlalchemy import func, select

    from app.catalog.listenbrainz_enrich import collect_listenbrainz_signals
    from app.database.catalog_models import CatalogSong
    from app.providers.listenbrainz.listenbrainz_provider import ListenBrainzProvider

    total = (await session.execute(select(func.count(CatalogSong.id)))).scalar_one()
    provider = ListenBrainzProvider()
    try:
        report = await collect_listenbrainz_signals(session, provider, total_songs=total)
    finally:
        await provider.aclose()

    logger.info("LISTENBRAINZ SIGNALS\n")
    logger.info(f"  songs in catalog      {total:>6}")
    logger.info(f"  with a MusicBrainz id {report.songs_with_mbid:>6}")
    logger.info(f"  without an MBID       {report.missing_mbid:>6}")
    logger.info(f"  answered with data    {report.answered:>6}")
    logger.info(f"  unique listener rows  {report.with_listeners:>6}")
    logger.info(f"  listen count rows     {report.with_listens:>6}")
    logger.info(f"  no data (not zero)    {report.no_data:>6}")
    logger.info(f"  failed                {report.failed:>6}")
    logger.info(f"  requests made         {provider.request_count:>6}")
    return 0


async def _popularity_report(session, settings, force: bool = False) -> int:
    from app.catalog.popularity_report import build_popularity_report, render_report

    logger.info(render_report(await build_popularity_report(session)))
    return 0


async def _recompute(session, settings, force: bool = False) -> int:
    from app.catalog.enrich import recompute_all_eligibility

    counts = await recompute_all_eligibility(session)
    logger.info(
        "recomputed %s songs, %s eligible for play", counts["total"], counts["eligible"]
    )
    return 0


def _render_enrichment(label: str, report) -> None:
    logger.info("\n%s ENRICHMENT\n", label)
    logger.info(f"  considered            {report.considered:>6}")
    logger.info(f"  already done          {report.skipped_already_done:>6}")
    logger.info(f"  matched               {report.matched:>6}")
    logger.info(f"  identifiers added     {report.identifiers_added:>6}")
    logger.info(f"  ISRCs added           {report.isrcs_added:>6}")
    logger.info(f"  genres added          {report.genres_added:>6}")
    logger.info(f"  unresolved            {report.unresolved:>6}")
    logger.info(f"  failed                {report.failed:>6}")

    if report.reasons:
        logger.info("\n  unresolved reasons:")
        for reason in report.reasons[:12]:
            logger.info(f"      {reason[:96]}")
        if len(report.reasons) > 12:
            logger.info(f"      ... and {len(report.reasons) - 12} more")


async def _stats(session, settings, force: bool = False) -> int:
    from app.catalog.validate import catalog_stats, render_stats

    logger.info(render_stats(await catalog_stats(session)))
    return 0


async def _validate(session, settings, force: bool = False) -> int:
    from app.catalog.validate import render_problems, validate_catalog

    problems = await validate_catalog(session, settings.audio_dir)
    logger.info(render_problems(problems))
    return 0


async def _unresolved(session, settings, force: bool = False) -> int:
    from sqlalchemy import select

    from app.database.catalog_models import UnresolvedMatch

    rows = (
        await session.execute(
            select(UnresolvedMatch).where(UnresolvedMatch.status == "pending")
        )
    ).scalars().all()

    if not rows:
        logger.info("no unresolved matches")
        return 0

    logger.info(f"{len(rows)} unresolved matches\n")
    for row in rows[:50]:
        logger.info(f"  [{row.provider}] {row.reason}")
    if len(rows) > 50:
        logger.info(f"  ... and {len(rows) - 50} more")
    return 0


async def _signal_report(session, settings, force: bool = False) -> int:
    from app.catalog.multisignal_report import (
        build_multisignal_report,
        render_multisignal_report,
    )

    logger.info(render_multisignal_report(await build_multisignal_report(session)))
    return 0


async def _model_comparison(session, settings, force: bool = False) -> int:
    from app.catalog.multisignal_report import (
        build_multisignal_report,
        render_model_comparison,
    )

    logger.info(render_model_comparison(await build_multisignal_report(session)))
    return 0


async def _audit_youtube(session, settings, force: bool = False) -> int:
    """Re-score stored YouTube matches. --force invalidates the failures."""
    from app.catalog.youtube_audit import audit_youtube_matches, render_audit

    report = await audit_youtube_matches(session, invalidate=force)
    logger.info(render_audit(report))
    if not force and report.checked != report.still_valid:
        logger.warning(
            "  Re-run with --force to invalidate these, then enrich-youtube to rematch."
        )
    return 0


COMMANDS = {
    "migrate-curated": _migrate_curated,
    "enrich-musicbrainz": _enrich_musicbrainz,
    "enrich-spotify": _enrich_spotify,
    "recompute": _recompute,
    "enrich-youtube": _enrich_youtube,
    "refresh-youtube": _refresh_youtube,
    "enrich-listenbrainz": _enrich_listenbrainz,
    "popularity-report": _popularity_report,
    "signal-report": _signal_report,
    "audit-youtube": _audit_youtube,
    "model-comparison": _model_comparison,
    "stats": _stats,
    "validate": _validate,
    "unresolved": _unresolved,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.catalog", description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-run enrichment on songs that already have the provider's id, "
        "for backfilling data a newer pass collects",
    )
    args = parser.parse_args(argv)

    return asyncio.run(_with_session(COMMANDS[args.command], args.force))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
