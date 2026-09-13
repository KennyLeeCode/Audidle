"""The architectural claim, as a test.

GameService must be able to run a complete round on an Audidle song without
touching MusicBrainz or Spotify. That is the whole point of the migration, and
a comment asserting it is worth much less than a test that fails if it stops
being true.

The mechanism is blunt on purpose: the catalog is backed by the real Audidle
providers, and any attempt to reach an external API raises. If starting a round,
playing it, guessing, or revealing the answer touches the network, these fail.
"""

import random
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog.writer import (
    attach_genres,
    attach_identifier,
    attach_isrc,
    recompute_eligibility,
    upsert_audio_asset,
    upsert_popularity,
    upsert_song,
)
from app.config.stages import FINAL_STAGE_INDEX
from app.database.models import Base
from app.models.enums import Difficulty, RoundOutcome
from app.models.song import ID_TYPE_TRACK, PROVIDER_AUDIDLE, PROVIDER_SPOTIFY
from app.providers.database.audidle_catalog import (
    AudidleAudioProvider,
    AudidlePopularityProvider,
    AudidleSongCatalogProvider,
)
from app.repositories.round_repository import InMemoryRoundRepository
from app.services.game_service import GameService
from app.services.recent_songs import RecentSongsTracker
from app.services.song_service import SongService


class ExplodingProvider:
    """Stands in for any external API. Reaching it is the failure."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attribute: str):
        async def boom(*args, **kwargs):
            raise AssertionError(
                f"the game reached {self.name}.{attribute} during a round. "
                f"Round creation and play must read only the Audidle catalog."
            )

        return boom


@pytest.fixture
async def catalog(tmp_path):
    """A small Audidle catalog with real audio on disk."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    tiers = [
        ("Blinding Lights", "The Weeknd", 4_700_000_000, "USUG11904206"),
        ("Shape of You", "Ed Sheeran", 4_200_000_000, "GBAHS1600463"),
        ("Space Song", "Beach House", 900_000_000, "USX9P1500001"),
        ("Motion Sickness", "Phoebe Bridgers", 250_000_000, "USX9P1700002"),
    ]

    async with factory() as session:
        for title, artist, streams, isrc in tiers:
            song = await upsert_song(
                session, song_id=None, title=title, artist_credit=artist, release_year=2020
            )
            await attach_identifier(
                session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, f"spotify-{song.id[:8]}"
            )
            await attach_isrc(session, song, isrc)
            await attach_genres(session, song, ["pop"], source="seed")
            await upsert_popularity(session, song, "manual-estimate", stream_count=streams)

            audio = tmp_path / f"{song.id}.wav"
            audio.write_bytes(b"RIFF0000WAVEfmt ")
            await upsert_audio_asset(
                session, song, "local", audio.name, playable=True
            )
            await recompute_eligibility(session, song)
        await session.commit()

    yield factory, tmp_path
    await engine.dispose()


@pytest.fixture
def game(catalog):
    """A GameService wired to the Audidle catalog and nothing else."""
    factory, audio_dir = catalog
    songs = SongService(
        catalog=AudidleSongCatalogProvider(factory),
        popularity=AudidlePopularityProvider(factory),
        audio=AudidleAudioProvider(factory, audio_dir),
        rng=random.Random(7),
    )
    return GameService(
        song_service=songs,
        round_repository=InMemoryRoundRepository(),
        recent_songs=RecentSongsTracker(history_size=10),
    )


# -- The claim ---------------------------------------------------------------


@pytest.mark.anyio
async def test_a_round_runs_entirely_on_the_audidle_catalog(game, catalog):
    """No MusicBrainz, no Spotify, start to reveal."""
    factory, audio_dir = catalog

    # Both external providers are replaced with objects that raise on any call.
    songs = SongService(
        catalog=AudidleSongCatalogProvider(factory),
        popularity=AudidlePopularityProvider(factory),
        audio=AudidleAudioProvider(factory, audio_dir),
        rng=random.Random(7),
    )
    service = GameService(
        song_service=songs,
        round_repository=InMemoryRoundRepository(),
        recent_songs=RecentSongsTracker(),
    )
    songs._spotify = ExplodingProvider("spotify")  # type: ignore[attr-defined]
    songs._musicbrainz = ExplodingProvider("musicbrainz")  # type: ignore[attr-defined]

    game_round, source = await service.start_round(Difficulty.EASY)
    assert source is not None

    # Walk to the final stage and win there.
    for _ in range(FINAL_STAGE_INDEX):
        await service.skip_stage(game_round.round_id)

    result = await service.submit_guess(
        game_round.round_id, PROVIDER_AUDIDLE, game_round.song_id
    )
    assert result.correct is True

    _, song, playback = await service.get_result(game_round.round_id)
    assert song.title
    assert playback is not None


@pytest.mark.anyio
async def test_round_songs_are_audidle_ids_not_provider_ids(game):
    game_round, _ = await game.start_round(Difficulty.EASY)

    # A UUID, not a Spotify track id.
    uuid.UUID(game_round.song_id)


@pytest.mark.anyio
async def test_a_spotify_guess_resolves_to_the_audidle_song(game, catalog):
    """The server side half of the guess flow.

    The client only ever holds provider ids. This is what turns one back into
    the Audidle song, and it is why search results can stay provider scoped.
    """
    factory, _ = catalog
    game_round, _ = await game.start_round(Difficulty.EASY)

    provider = AudidleSongCatalogProvider(factory)
    answer = await provider.get_song(game_round.song_id)
    spotify_id = answer.spotify_id
    assert spotify_id is not None

    result = await game.submit_guess(game_round.round_id, PROVIDER_SPOTIFY, spotify_id)

    assert result.correct is True
    assert result.game_round.outcome is RoundOutcome.WON


@pytest.mark.anyio
async def test_an_unknown_provider_id_is_a_normal_wrong_guess(game):
    game_round, _ = await game.start_round(Difficulty.EASY)

    result = await game.submit_guess(game_round.round_id, PROVIDER_SPOTIFY, "not-in-catalog")

    assert result.correct is False
    assert result.game_round.stage_index == 1
    # And the song did not change.
    assert result.game_round.song_id == game_round.song_id


# -- Selection ---------------------------------------------------------------


@pytest.mark.anyio
async def test_only_eligible_songs_are_selectable(game, catalog):
    """An ineligible song must never be drawn, however complete it looks."""
    factory, _ = catalog

    async with factory() as session:
        song = await upsert_song(
            session, song_id=None, title="No Audio", artist_credit="Someone"
        )
        await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "sp-none")
        await upsert_popularity(
            session, song, "manual-estimate", stream_count=3_000_000_000
        )
        # Deliberately no audio asset.
        record = await recompute_eligibility(session, song)
        await session.commit()

    assert record.eligible is False

    drawn = set()
    for _ in range(12):
        game_round, _ = await game.start_round(Difficulty.EASY)
        drawn.add(game_round.song_id)

    assert song.id not in drawn


@pytest.mark.anyio
async def test_recent_song_prevention_works_on_the_new_catalog(game):
    """Consecutive rounds in a session should not repeat."""
    seen = []
    for _ in range(2):
        game_round, _ = await game.start_round(Difficulty.EASY, session_id="s1")
        seen.append(game_round.song_id)

    assert len(set(seen)) == len(seen)


@pytest.mark.anyio
async def test_difficulty_selects_from_the_right_tier(game, catalog):
    factory, _ = catalog
    provider = AudidleSongCatalogProvider(factory)

    game_round, _ = await game.start_round(Difficulty.HARD)
    song = await provider.get_song(game_round.song_id)

    assert song.title == "Motion Sickness"


@pytest.mark.anyio
async def test_the_catalog_finds_songs_by_isrc(catalog):
    """The preferred cross-provider key."""
    factory, _ = catalog
    provider = AudidleSongCatalogProvider(factory)

    song = await provider.find_by_isrc("usug11904206")

    assert song is not None
    assert song.title == "Blinding Lights"
