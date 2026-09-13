"""Catalog admin commands.

    py -m app.catalog migrate-curated   move the old curated table into the catalog
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


async def _migrate_curated(session, settings) -> int:
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


async def _stats(session, settings) -> int:
    from app.catalog.validate import catalog_stats, render_stats

    logger.info(render_stats(await catalog_stats(session)))
    return 0


async def _validate(session, settings) -> int:
    from app.catalog.validate import render_problems, validate_catalog

    problems = await validate_catalog(session, settings.audio_dir)
    logger.info(render_problems(problems))
    return 0


async def _unresolved(session, settings) -> int:
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


COMMANDS = {
    "migrate-curated": _migrate_curated,
    "stats": _stats,
    "validate": _validate,
    "unresolved": _unresolved,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.catalog", description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)

    return asyncio.run(_with_session(COMMANDS[args.command]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
