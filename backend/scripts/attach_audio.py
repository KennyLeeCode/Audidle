"""Attach audio files to curated songs.

Ingest resolves identity and difficulty. It cannot resolve audio, because
Spotify does not provide any. This script is the seam where audio you have
obtained legally gets associated with a curated row, and until a row has audio
it is never offered for selection.

Two modes:

  --from-dir DIR
      Scan a directory and match files to catalog rows. A file named after a
      track id (4uLU6hMCjMI75M1A2tKUQC.mp3) matches directly. Otherwise the
      filename is matched against the title and artist using the same
      normalization the guess matcher uses, so "Queen - Bohemian Rhapsody.mp3"
      resolves.

  --placeholder
      DEVELOPMENT ONLY. Generates a synthesized tone per curated song so the
      full pipeline can be exercised end to end. The audio has nothing to do
      with the real track, so the game is not winnable in any meaningful sense.
      Use it to verify plumbing, never to play.

Run from the backend directory:

    py scripts/attach_audio.py --from-dir ~/music/audidle
    py scripts/attach_audio.py --placeholder
    py scripts/attach_audio.py --status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.config.settings import get_settings  # noqa: E402
from app.database.database import (  # noqa: E402
    build_engine,
    build_session_factory,
    create_schema,
    session_scope,
)
from app.database.models import CuratedSong  # noqa: E402
from app.services.guess_matcher import normalize_artist, normalize_title  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("attach-audio")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac"}


def filename_key(stem: str) -> str:
    """Normalize a filename into something comparable to title and artist."""
    return normalize_title(stem.replace("_", " ").replace("-", " "))


async def attach_from_directory(directory: Path, audio_dir: Path) -> int:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    await create_schema(engine)

    files = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]
    logger.info("found %s audio files in %s", len(files), directory)

    matched = 0
    async with session_scope(factory) as session:
        rows = (await session.execute(select(CuratedSong))).scalars().all()

        by_track_id = {row.track_id: row for row in rows}
        # Both directions of "artist title" and "title artist", since file
        # naming conventions vary.
        by_name: dict[str, CuratedSong] = {}
        for row in rows:
            title = normalize_title(row.title)
            artist = normalize_artist(row.artist)
            by_name[f"{artist} {title}"] = row
            by_name[f"{title} {artist}"] = row
            by_name[title] = row

        for path in files:
            row = by_track_id.get(path.stem) or by_name.get(filename_key(path.stem))
            if row is None:
                logger.warning("  no catalog match for %s", path.name)
                continue

            # Stored relative to the audio directory, so the database stays
            # portable across machines and deployments.
            destination = audio_dir / f"{row.track_id}{path.suffix.lower()}"
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())

            row.audio_file = destination.name
            matched += 1
            logger.info("  %s -> %s", path.name[:44], row.track_id)

    await engine.dispose()
    logger.info("attached audio to %s songs", matched)
    return 0


async def attach_placeholders(audio_dir: Path) -> int:
    """Generate synthesized audio so the pipeline can be exercised.

    Loudly development only. See the module docstring.
    """
    import wave

    from generate_mock_audio import SAMPLE_RATE, render_track  # type: ignore[import-not-found]

    logger.warning(
        "PLACEHOLDER MODE: generating synthesized tones. This audio is unrelated to the "
        "real tracks, so the game is not meaningfully winnable. Development only."
    )

    settings = get_settings()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    await create_schema(engine)

    audio_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    async with session_scope(factory) as session:
        rows = (await session.execute(select(CuratedSong))).scalars().all()
        for index, row in enumerate(rows):
            name = f"{row.track_id}.wav"
            target = audio_dir / name
            if not target.exists():
                with wave.open(str(target), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(SAMPLE_RATE)
                    handle.writeframes(render_track(index))
            row.audio_file = name
            count += 1

    await engine.dispose()
    logger.info("generated placeholder audio for %s songs", count)
    return 0


async def show_status() -> int:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    await create_schema(engine)

    async with session_scope(factory) as session:
        statement = select(
            CuratedSong.difficulty,
            func.count().label("total"),
            func.count(CuratedSong.audio_file).label("playable"),
        ).group_by(CuratedSong.difficulty)
        rows = (await session.execute(statement)).all()

    await engine.dispose()

    logger.info(f"{'tier':14}{'playable':>10}{'total':>8}")
    for difficulty, total, playable in rows:
        flag = "" if playable else "   <- no rounds possible"
        logger.info(f"{difficulty:14}{playable:>10}{total:>8}{flag}")
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-dir", type=Path, help="directory of audio files to attach")
    group.add_argument(
        "--placeholder",
        action="store_true",
        help="generate synthesized audio (development only, not real tracks)",
    )
    group.add_argument("--status", action="store_true", help="report audio coverage per tier")
    args = parser.parse_args()

    if args.status:
        return asyncio.run(show_status())
    if args.placeholder:
        sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
        return asyncio.run(attach_placeholders(settings.audio_dir))

    if not args.from_dir.is_dir():
        logger.error("not a directory: %s", args.from_dir)
        return 1
    return asyncio.run(attach_from_directory(args.from_dir, settings.audio_dir))


if __name__ == "__main__":
    raise SystemExit(main())
