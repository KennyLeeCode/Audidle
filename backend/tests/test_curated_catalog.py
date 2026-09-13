"""Tests for the curated catalog database layer.

Run against an in memory SQLite database built per test, so they need no
credentials, no network, and no ingested data.

The behaviour worth protecting here is the audio gate. Ingest produces rows with
identity and difficulty but no audio, and offering those for selection would
hand the player a round with nothing to hear. The provider filters them out, and
these tests pin that down.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base, CuratedSong
from app.models.enums import Difficulty
from app.models.song import SongSelectionCriteria
from app.providers.database.db_audio_provider import DbAudioProvider
from app.providers.database.db_catalog import (
    CompositeSongCatalogProvider,
    DbPopularityProvider,
    DbSongCatalogProvider,
)


@pytest.fixture
async def sessions():
    """A fresh in memory database per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                CuratedSong(
                    track_id="easy-with-audio",
                    isrc="AAA111111111",
                    title="Blinding Lights",
                    artist="The Weeknd",
                    album="After Hours",
                    release_year=2020,
                    genres="pop,synthpop",
                    stream_estimate=4_700_000_000,
                    difficulty=Difficulty.EASY,
                    audio_file="easy.wav",
                ),
                CuratedSong(
                    track_id="easy-no-audio",
                    title="Shape of You",
                    artist="Ed Sheeran",
                    genres="pop",
                    stream_estimate=4_200_000_000,
                    difficulty=Difficulty.EASY,
                    audio_file=None,
                ),
                CuratedSong(
                    track_id="hard-with-audio",
                    title="Motion Sickness",
                    artist="Phoebe Bridgers",
                    genres="indie,folk",
                    release_year=2017,
                    stream_estimate=250_000_000,
                    difficulty=Difficulty.HARD,
                    audio_file="hard.wav",
                ),
            ]
        )
        await session.commit()

    yield factory
    await engine.dispose()


# -- Catalog ----------------------------------------------------------------


@pytest.mark.anyio
async def test_maps_a_row_onto_the_domain_model(sessions):
    provider = DbSongCatalogProvider(sessions)

    song = await provider.get_song("easy-with-audio")

    assert song is not None
    assert song.title == "Blinding Lights"
    assert song.isrc == "AAA111111111"
    assert song.genres == ("pop", "synthpop")


@pytest.mark.anyio
async def test_unknown_track_returns_none(sessions):
    provider = DbSongCatalogProvider(sessions)

    assert await provider.get_song("nope") is None


@pytest.mark.anyio
async def test_search_matches_title_or_artist(sessions):
    provider = DbSongCatalogProvider(sessions)

    assert len(await provider.search("blinding")) == 1
    assert len(await provider.search("phoebe")) == 1
    assert await provider.search("nothing here") == []


@pytest.mark.anyio
async def test_batch_lookup_omits_unknown_ids(sessions):
    provider = DbSongCatalogProvider(sessions)

    songs = await provider.get_songs(["easy-with-audio", "does-not-exist"])

    assert len(songs) == 1


# -- Popularity and the audio gate ------------------------------------------


@pytest.mark.anyio
async def test_selection_excludes_songs_without_audio(sessions):
    """The rule that keeps unplayable rounds from ever being created."""
    provider = DbPopularityProvider(sessions, require_audio=True)

    eligible = await provider.get_eligible_track_ids(
        SongSelectionCriteria(difficulty=Difficulty.EASY)
    )

    assert eligible == ["easy-with-audio"]


@pytest.mark.anyio
async def test_the_audio_gate_can_be_turned_off(sessions):
    provider = DbPopularityProvider(sessions, require_audio=False)

    eligible = await provider.get_eligible_track_ids(
        SongSelectionCriteria(difficulty=Difficulty.EASY)
    )

    assert set(eligible) == {"easy-with-audio", "easy-no-audio"}


@pytest.mark.anyio
async def test_selection_respects_difficulty_and_exclusions(sessions):
    provider = DbPopularityProvider(sessions, require_audio=True)

    assert await provider.get_eligible_track_ids(
        SongSelectionCriteria(difficulty=Difficulty.HARD)
    ) == ["hard-with-audio"]

    assert (
        await provider.get_eligible_track_ids(
            SongSelectionCriteria(
                difficulty=Difficulty.HARD,
                exclude_track_ids=frozenset({"hard-with-audio"}),
            )
        )
        == []
    )


@pytest.mark.anyio
async def test_popularity_reports_the_stored_tier(sessions):
    provider = DbPopularityProvider(sessions)

    popularity = await provider.get_popularity("hard-with-audio")

    assert popularity is not None
    assert popularity.difficulty is Difficulty.HARD
    assert popularity.stream_estimate == 250_000_000


# -- Audio ------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_playable_source_when_no_audio_is_attached(sessions, tmp_path):
    provider = DbAudioProvider(sessions, tmp_path)

    assert await provider.get_playable_source("easy-no-audio") is None


@pytest.mark.anyio
async def test_no_playable_source_when_the_file_is_missing(sessions, tmp_path):
    """A row claiming audio that is not on disk is unplayable, not an error."""
    provider = DbAudioProvider(sessions, tmp_path)

    assert await provider.get_playable_source("easy-with-audio") is None


@pytest.mark.anyio
async def test_playable_source_when_the_file_exists(sessions, tmp_path):
    (tmp_path / "easy.wav").write_bytes(b"RIFF....WAVEfmt ")
    provider = DbAudioProvider(sessions, tmp_path)

    source = await provider.get_playable_source("easy-with-audio")

    assert source is not None
    # Local files decode, so the 0.01s stage is exact.
    assert source.supports_precise_clips is True
    # The filename never leaves the backend, the API layer supplies a round
    # scoped URL instead.
    assert source.url is None


@pytest.mark.anyio
async def test_audio_paths_cannot_escape_the_audio_directory(sessions, tmp_path):
    """A crafted filename must not turn the audio route into a file reader."""
    async with sessions() as session:
        row = await session.get(CuratedSong, "easy-with-audio")
        row.audio_file = "../../../etc/passwd"
        await session.commit()

    provider = DbAudioProvider(sessions, tmp_path / "audio")

    assert await provider.get_playable_source("easy-with-audio") is None


# -- Composite --------------------------------------------------------------


class _FallbackProvider(DbSongCatalogProvider):
    """Stands in for Spotify in the composite."""

    def __init__(self) -> None:
        self.searched = False
        self.requested: list[str] = []

    async def search(self, query: str, limit: int = 10):
        self.searched = True
        return []

    async def get_song(self, track_id: str):
        self.requested.append(track_id)
        return None

    async def get_songs(self, track_ids: list[str]):
        self.requested.extend(track_ids)
        return []


@pytest.mark.anyio
async def test_composite_searches_upstream_but_reads_metadata_locally(sessions):
    fallback = _FallbackProvider()
    composite = CompositeSongCatalogProvider(
        search_provider=fallback, metadata_provider=DbSongCatalogProvider(sessions)
    )

    await composite.search("anything")
    song = await composite.get_song("easy-with-audio")

    assert fallback.searched is True
    assert song is not None
    # Resolved locally, so no upstream call was needed.
    assert fallback.requested == []


@pytest.mark.anyio
async def test_composite_falls_through_for_uncurated_songs(sessions):
    """A player guessing a song outside the curated set still resolves."""
    fallback = _FallbackProvider()
    composite = CompositeSongCatalogProvider(
        search_provider=fallback, metadata_provider=DbSongCatalogProvider(sessions)
    )

    await composite.get_song("some-spotify-id")

    assert fallback.requested == ["some-spotify-id"]
