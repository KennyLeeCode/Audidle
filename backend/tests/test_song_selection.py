"""Tests for criteria based song selection.

Covers the filter architecture and the playability guarantee, which is what
keeps a player from ever being handed a round with no audio.
"""

import random

import pytest

from app.core.errors import NoEligibleSongError, NoPlayableSongError
from app.models.enums import Difficulty
from app.models.song import PlayableSource, Song, SongSelectionCriteria
from app.providers.base import AudioProvider
from app.providers.mock import MockPopularityProvider, MockSongProvider
from app.services.song_service import SongService


class _DeadAudioProvider(AudioProvider):
    """Every track is unplayable. Simulates a broken audio backend."""

    async def get_playable_source(self, track_id: str) -> PlayableSource | None:
        return None

    async def open_stream(self, track_id: str) -> tuple[bytes, str] | None:
        return None


@pytest.mark.anyio
async def test_selection_respects_difficulty(song_service):
    criteria = SongSelectionCriteria(difficulty=Difficulty.IMPOSSIBLE)

    song, _ = await song_service.pick_playable_song(criteria)

    assert song.track_id


@pytest.mark.anyio
async def test_selection_excludes_recent_songs(song_service):
    first = SongSelectionCriteria(difficulty=Difficulty.EASY)
    song, _ = await song_service.pick_playable_song(first)

    second = SongSelectionCriteria(
        difficulty=Difficulty.EASY, exclude_track_ids=frozenset({song.track_id})
    )
    other, _ = await song_service.pick_playable_song(second)

    assert other.track_id != song.track_id


@pytest.mark.anyio
async def test_exclusion_is_relaxed_rather_than_failing(song_service, catalog_path):
    """A repeat beats a dead end when the whole pool is excluded."""
    provider = MockPopularityProvider(catalog_path)
    everything = await provider.get_eligible_track_ids(
        SongSelectionCriteria(difficulty=Difficulty.EASY)
    )

    criteria = SongSelectionCriteria(
        difficulty=Difficulty.EASY, exclude_track_ids=frozenset(everything)
    )
    song, _ = await song_service.pick_playable_song(criteria)

    assert song.track_id in everything


@pytest.mark.anyio
async def test_unplayable_songs_raise_rather_than_returning_a_broken_round(
    settings, catalog_path
):
    service = SongService(
        catalog=MockSongProvider(catalog_path),
        popularity=MockPopularityProvider(catalog_path),
        audio=_DeadAudioProvider(),
        max_attempts=5,
        rng=random.Random(1),
    )

    with pytest.raises(NoPlayableSongError):
        await service.pick_playable_song(SongSelectionCriteria(difficulty=Difficulty.EASY))


@pytest.mark.anyio
async def test_impossible_filter_combination_raises(song_service):
    criteria = SongSelectionCriteria(
        difficulty=Difficulty.EASY, genres=("gregorian-chant",)
    )

    with pytest.raises(NoEligibleSongError):
        await song_service.pick_playable_song(criteria)


@pytest.mark.anyio
async def test_genre_filter_narrows_the_pool(song_service):
    criteria = SongSelectionCriteria(difficulty=Difficulty.EASY, genres=("electronic",))

    song, _ = await song_service.pick_playable_song(criteria)

    assert "electronic" in song.genres


def test_criteria_matching_is_pure():
    """The filter predicate should be testable without any provider."""
    song = Song(
        track_id="x",
        title="T",
        artist="A",
        release_year=1997,
        explicit=True,
        genres=("rock",),
    )

    assert song.decade == 1990
    assert SongSelectionCriteria(difficulty=Difficulty.EASY).matches(song)
    assert not SongSelectionCriteria(difficulty=Difficulty.EASY, decades=(2000,)).matches(song)
    assert not SongSelectionCriteria(difficulty=Difficulty.EASY, explicit=False).matches(song)
    assert SongSelectionCriteria(difficulty=Difficulty.EASY, genres=("rock",)).matches(song)
