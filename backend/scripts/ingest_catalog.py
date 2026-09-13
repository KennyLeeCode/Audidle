"""Resolve the seed catalog against Spotify and write it to the database.

This is the pipeline that turns a hand written list of songs into rows keyed by
real Spotify track ids, which is what the game needs in order to run on real
metadata. It exists because Spotify supplies identity but not popularity, and
our seed file supplies popularity but not identity. Ingest joins the two.

For each seed row it:

  1. Searches Spotify for the title and artist.
  2. Picks the best match, preferring the original release over remasters and
     re-recordings, since those tend to rank higher in search but are not what
     a player pictures.
  3. Reads the track id, ISRC, artwork, album, and release year.
  4. Classifies the stream estimate into a difficulty tier.
  5. Upserts the row.

Run from the backend directory:

    py scripts/ingest_catalog.py                 # ingest everything
    py scripts/ingest_catalog.py --dry-run       # resolve only, write nothing
    py scripts/ingest_catalog.py --seed FILE     # use a different seed file

Refreshing the stream data later: replace app/data/seed_catalog.json with
figures from a real chart source, keeping the same shape, then rerun. Nothing
else in the application needs to change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.config.difficulties import difficulty_for_streams  # noqa: E402
from app.config.settings import get_settings  # noqa: E402
from app.database.database import (  # noqa: E402
    build_engine,
    build_session_factory,
    create_schema,
    session_scope,
)
from app.database.models import CuratedSong  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.providers.spotify.spotify_client import SpotifyClient  # noqa: E402
from app.providers.spotify.spotify_song_provider import SpotifySongProvider  # noqa: E402
from app.services.guess_matcher import normalize_artist, normalize_title  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("ingest")

# Wording that marks a release as a variant rather than the original. Matches
# are penalized so the album version wins where both exist.
VARIANT_MARKERS = (
    "remaster",
    "live",
    "acoustic",
    "demo",
    "instrumental",
    "karaoke",
    "sped up",
    "slowed",
    "remix",
    "cover",
    "tribute",
    "re-recorded",
)


@dataclass
class Resolution:
    """One seed row after a resolution attempt."""

    seed: dict
    song: Song | None
    reason: str = ""


def score_candidate(candidate: Song, seed: dict) -> int:
    """Rank how well a Spotify result matches a seed row.

    Higher is better. Negative means reject. The scoring exists because Spotify
    search often ranks a remaster or a sped up edit above the original, and
    ingesting those would quietly change which recording the game plays.
    """
    wanted_title = normalize_title(seed["title"])
    wanted_artist = normalize_artist(seed["artist"])

    if normalize_artist(candidate.artist) != wanted_artist:
        # Wrong artist is disqualifying. This is what stops a cover being
        # ingested in place of the original.
        return -1

    score = 0
    candidate_title = normalize_title(candidate.title)
    if candidate_title == wanted_title:
        score += 100
    elif wanted_title in candidate_title:
        score += 40
    else:
        return -1

    lowered = candidate.title.lower()
    for marker in VARIANT_MARKERS:
        if marker in lowered:
            score -= 30

    # Prefer entries that carry an ISRC, since those make guess matching work
    # across duplicate releases.
    if candidate.isrc:
        score += 5

    return score


async def resolve(provider: SpotifySongProvider, seed: dict) -> Resolution:
    """Find the best Spotify match for one seed row."""
    query = f"track:{seed['title']} artist:{seed['artist']}"

    try:
        candidates = await provider.search(query, limit=10)
    except Exception as error:
        return Resolution(seed, None, f"search failed: {error}")

    if not candidates:
        # Field qualified search is precise but brittle. A plain query catches
        # rows where the punctuation or credit differs.
        try:
            candidates = await provider.search(f"{seed['title']} {seed['artist']}", limit=10)
        except Exception as error:
            return Resolution(seed, None, f"search failed: {error}")

    scored = [(score_candidate(song, seed), song) for song in candidates]
    viable = [(score, song) for score, song in scored if score >= 0]

    if not viable:
        return Resolution(seed, None, "no candidate matched the title and artist")

    viable.sort(key=lambda pair: -pair[0])
    return Resolution(seed, viable[0][1])


async def ingest(seed_path: Path, dry_run: bool) -> int:
    settings = get_settings()

    if not settings.spotify_configured:
        logger.error("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env")
        return 1

    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    seeds = payload["songs"]
    meta = payload.get("meta", {})
    logger.info("resolving %s songs from %s", len(seeds), seed_path.name)

    client = SpotifyClient(settings.spotify_client_id, settings.spotify_client_secret)
    provider = SpotifySongProvider(client=client, market=settings.spotify_market)

    resolutions: list[Resolution] = []
    try:
        for index, seed in enumerate(seeds, start=1):
            result = await resolve(provider, seed)
            resolutions.append(result)

            if result.song:
                logger.info(
                    "  [%3d/%d] %-34s -> %s", index, len(seeds), seed["title"][:34],
                    result.song.track_id,
                )
            else:
                logger.warning(
                    "  [%3d/%d] %-34s -> UNRESOLVED (%s)", index, len(seeds),
                    seed["title"][:34], result.reason,
                )
    finally:
        await client.aclose()

    resolved = [r for r in resolutions if r.song]
    logger.info("\nresolved %s of %s", len(resolved), len(seeds))

    # Two different songs resolving to one track id would silently shrink the
    # catalog, so it is reported rather than swallowed by the upsert.
    seen: dict[str, str] = {}
    for result in resolved:
        assert result.song is not None
        existing = seen.get(result.song.track_id)
        if existing:
            logger.warning(
                "  duplicate track id %s shared by %r and %r",
                result.song.track_id, existing, result.seed["title"],
            )
        seen[result.song.track_id] = result.seed["title"]

    if dry_run:
        logger.info("dry run, nothing written")
        return 0

    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    await create_schema(engine)

    written = 0
    async with session_scope(factory) as session:
        for result in resolved:
            song = result.song
            assert song is not None
            seed = result.seed

            existing = await session.get(CuratedSong, song.track_id)
            row = existing or CuratedSong(track_id=song.track_id)

            row.isrc = song.isrc
            row.title = song.title
            row.artist = song.artist
            row.album = song.album
            row.artwork_url = song.artwork_url
            row.external_url = song.external_url
            row.release_year = song.release_year
            row.duration_ms = song.duration_ms
            row.explicit = song.explicit
            # Genres come from the seed file, since Spotify supplies none.
            row.genres = ",".join(seed.get("genres", []))
            row.stream_estimate = seed["stream_estimate"]
            row.difficulty = difficulty_for_streams(seed["stream_estimate"])
            row.source = meta.get("source", "manual-estimate")
            row.confidence = seed.get("confidence", "medium")
            row.streams_as_of = meta.get("streams_as_of")
            # Audio is assigned separately. A row without it is skipped during
            # selection, which is how the game avoids unplayable rounds.

            if existing is None:
                session.add(row)
            written += 1

        total = len((await session.execute(select(CuratedSong.track_id))).all())

    await engine.dispose()

    logger.info("wrote %s rows, %s total in the catalog", written, total)

    tier_counts: dict[str, int] = {}
    for result in resolved:
        tier = difficulty_for_streams(result.seed["stream_estimate"]).value
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    logger.info("tiers: %s", tier_counts)

    unplayable = total
    logger.warning(
        "\nNOTE: %s of %s rows have no audio file, so no round can be created from "
        "them yet. Spotify cannot supply audio. Attach audio with "
        "scripts/attach_audio.py before switching AUDIO_PROVIDER away from mock.",
        unplayable, total,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed",
        type=Path,
        default=BACKEND_ROOT / "app" / "data" / "seed_catalog.json",
        help="seed catalog JSON file",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="resolve against Spotify but write nothing"
    )
    args = parser.parse_args()

    if not args.seed.exists():
        logger.error("seed file not found: %s", args.seed)
        return 1

    return asyncio.run(ingest(args.seed, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
