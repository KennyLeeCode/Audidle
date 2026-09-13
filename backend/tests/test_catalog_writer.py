"""Tests for catalog writes, deduplication, and eligibility.

The theme is idempotency. Ingestion is resumable and gets rerun constantly, so
every write here must be safe to repeat. A test that only checks "the row was
created" would miss the failure that actually matters, which is the second run
creating a second copy.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog.genres import slugify_genre
from app.catalog.writer import (
    attach_genres,
    attach_identifier,
    attach_isrc,
    find_song_by_identifier,
    find_song_by_isrc,
    link_artist,
    recompute_eligibility,
    record_unresolved,
    upsert_artist,
    upsert_audio_asset,
    upsert_popularity,
    upsert_song,
)
from app.database.catalog_models import (
    Artist,
    AudioAsset,
    CatalogSong,
    ExternalIdentifierRow,
    Genre,
    SongGenre,
    SongPopularityRow,
    UnresolvedMatch,
)
from app.database.models import Base
from app.models.enums import Difficulty
from app.models.song import (
    ID_TYPE_RECORDING_MBID,
    ID_TYPE_TRACK,
    PROVIDER_MUSICBRAINZ,
    PROVIDER_SPOTIFY,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        yield active
    await engine.dispose()


async def make_song(session, title="Blinding Lights", artist="The Weeknd"):
    return await upsert_song(session, song_id=None, title=title, artist_credit=artist)


async def count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# -- Identity ---------------------------------------------------------------


@pytest.mark.anyio
async def test_songs_get_an_audidle_id(session):
    song = await make_song(session)

    # A UUID, not a provider id. This is the whole point of the migration.
    assert len(song.id) == 36
    assert song.id.count("-") == 4


@pytest.mark.anyio
async def test_a_song_can_hold_several_identifiers_including_two_isrcs(session):
    """A recording legitimately carries more than one ISRC."""
    song = await make_song(session)

    await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "spotify-1")
    await attach_identifier(
        session, song, PROVIDER_MUSICBRAINZ, ID_TYPE_RECORDING_MBID, "mbid-1"
    )
    await attach_isrc(session, song, "USUG11904206")
    await attach_isrc(session, song, "GBAHS1600463")

    assert await count(session, ExternalIdentifierRow) == 4


@pytest.mark.anyio
async def test_attaching_the_same_identifier_twice_is_a_no_op(session):
    song = await make_song(session)

    await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "abc")
    await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "abc")

    assert await count(session, ExternalIdentifierRow) == 1


@pytest.mark.anyio
async def test_an_identifier_cannot_be_stolen_by_a_second_song(session):
    """Two songs claiming one Spotify id means an earlier match was wrong.

    Repointing it silently would corrupt whichever one is correct, so the
    attach is refused instead.
    """
    first = await make_song(session)
    second = await make_song(session, title="Save Your Tears")

    await attach_identifier(session, first, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "shared")
    result = await attach_identifier(
        session, second, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "shared"
    )

    assert result is None
    assert await count(session, ExternalIdentifierRow) == 1


@pytest.mark.anyio
async def test_songs_are_found_by_any_identifier(session):
    """The lookup that prevents ingestion from creating duplicates."""
    song = await make_song(session)
    await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "sp-1")
    await attach_isrc(session, song, "USUG11904206")

    assert (await find_song_by_identifier(session, PROVIDER_SPOTIFY, "sp-1")).id == song.id
    assert (await find_song_by_isrc(session, "USUG11904206")).id == song.id
    assert await find_song_by_isrc(session, "NOTREAL00000") is None


@pytest.mark.anyio
async def test_isrcs_are_normalized_to_uppercase(session):
    song = await make_song(session)

    await attach_isrc(session, song, "  usug11904206 ")

    assert (await find_song_by_isrc(session, "USUG11904206")).id == song.id


# -- Artists ----------------------------------------------------------------


@pytest.mark.anyio
async def test_artists_are_reused_rather_than_duplicated(session):
    first = await upsert_artist(session, "The Weeknd")
    second = await upsert_artist(session, "The Weeknd")

    assert first.id == second.id
    assert await count(session, Artist) == 1


@pytest.mark.anyio
async def test_artists_match_across_accent_spellings(session):
    """The same fold the guess matcher uses, so "Beyonce" is not a new artist."""
    first = await upsert_artist(session, "Beyoncé")
    second = await upsert_artist(session, "Beyonce")

    assert first.id == second.id


@pytest.mark.anyio
async def test_linking_an_artist_twice_is_a_no_op(session):
    song = await make_song(session)
    artist = await upsert_artist(session, "The Weeknd")

    await link_artist(session, song, artist, position=0)
    await link_artist(session, song, artist, position=0)

    assert len((await session.execute(select(CatalogSong))).scalars().all()) == 1


# -- Genres -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hip Hop", "hip-hop"),
        ("hip-hop", "hip-hop"),
        ("hip hop", "hip-hop"),
        ("HIPHOP", "hip-hop"),
        ("R&B", "r-and-b"),
        ("RnB", "r-and-b"),
        ("Synth-Pop", "synthpop"),
        ("  Indie  ", "indie"),
        ("Drum & Bass", "drum-and-bass"),
        ("!!!", ""),
    ],
)
def test_genre_slugs_fold_spelling_differences(raw, expected):
    assert slugify_genre(raw) == expected


def test_genre_slugs_do_not_merge_real_subgenres():
    """Folding spelling is fine, collapsing distinct genres is not."""
    assert slugify_genre("house") != slugify_genre("deep house")
    assert slugify_genre("rock") != slugify_genre("indie rock")
    assert slugify_genre("metal") != slugify_genre("black metal")


@pytest.mark.anyio
async def test_genre_variants_collapse_onto_one_row(session):
    song = await make_song(session)

    await attach_genres(session, song, ["Hip Hop", "hip-hop", "HIPHOP"], source="seed")

    assert await count(session, Genre) == 1
    assert await count(session, SongGenre) == 1


@pytest.mark.anyio
async def test_missing_genre_data_invents_nothing(session):
    song = await make_song(session)

    attached = await attach_genres(session, song, ["", "   ", "!!!"], source="seed")

    assert attached == 0
    assert await count(session, Genre) == 0


# -- Popularity and audio ---------------------------------------------------


@pytest.mark.anyio
async def test_popularity_is_one_row_per_provider(session):
    song = await make_song(session)

    await upsert_popularity(session, song, "manual-estimate", stream_count=1_000)
    await upsert_popularity(session, song, "manual-estimate", stream_count=2_000)
    await upsert_popularity(session, song, "chart-source", stream_count=3_000)

    rows = (await session.execute(select(SongPopularityRow))).scalars().all()
    assert len(rows) == 2
    # The rerun updated rather than appended.
    assert {row.stream_count for row in rows} == {2_000, 3_000}


@pytest.mark.anyio
async def test_audio_assets_are_not_duplicated_on_rerun(session):
    song = await make_song(session)

    await upsert_audio_asset(session, song, "local", "abc.wav", playable=True)
    await upsert_audio_asset(session, song, "local", "abc.wav", playable=True)

    assert await count(session, AudioAsset) == 1


# -- Eligibility ------------------------------------------------------------


@pytest.mark.anyio
async def test_a_song_with_no_audio_is_not_eligible(session):
    """Knowing about a song is not the same as being able to play it."""
    song = await make_song(session)
    await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "sp")
    await upsert_popularity(session, song, "manual-estimate", stream_count=2_000_000_000)

    record = await recompute_eligibility(session, song)

    assert record.has_identity is True
    assert record.has_difficulty is True
    assert record.has_audio is False
    assert record.eligible is False


@pytest.mark.anyio
async def test_unvalidated_audio_does_not_make_a_song_eligible(session):
    song = await make_song(session)
    await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "sp")
    await upsert_popularity(session, song, "manual-estimate", stream_count=2_000_000_000)
    await upsert_audio_asset(session, song, "local", "missing.wav", playable=False)

    record = await recompute_eligibility(session, song)

    assert record.has_audio is True
    assert record.audio_playable is False
    assert record.eligible is False


@pytest.mark.anyio
async def test_a_song_with_no_popularity_is_not_eligible(session):
    song = await make_song(session)
    await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "sp")
    await upsert_audio_asset(session, song, "local", "a.wav", playable=True)

    record = await recompute_eligibility(session, song)

    assert record.has_difficulty is False
    assert record.eligible is False


@pytest.mark.anyio
async def test_a_complete_song_is_eligible_and_tiered(session):
    song = await make_song(session)
    await attach_identifier(session, song, PROVIDER_SPOTIFY, ID_TYPE_TRACK, "sp")
    await upsert_popularity(session, song, "manual-estimate", stream_count=2_000_000_000)
    await upsert_audio_asset(session, song, "local", "a.wav", playable=True)

    record = await recompute_eligibility(session, song)

    assert record.eligible is True
    assert record.difficulty is Difficulty.EASY


@pytest.mark.anyio
async def test_difficulty_follows_the_stream_estimate(session):
    cases = [
        (2_000_000_000, Difficulty.EASY),
        (700_000_000, Difficulty.MEDIUM),
        (200_000_000, Difficulty.HARD),
        (50_000_000, Difficulty.EXPERT),
        (1_000_000, Difficulty.IMPOSSIBLE),
    ]
    for streams, expected in cases:
        song = await make_song(session, title=f"Song {streams}")
        await upsert_popularity(session, song, "manual-estimate", stream_count=streams)
        record = await recompute_eligibility(session, song)
        assert record.difficulty is expected


# -- Unresolved matches -----------------------------------------------------


@pytest.mark.anyio
async def test_unresolved_matches_are_not_piled_up_on_rerun(session):
    song = await make_song(session)

    await record_unresolved(session, "musicbrainz", "no confident match", {}, song)
    await record_unresolved(session, "musicbrainz", "no confident match", {}, song)

    assert await count(session, UnresolvedMatch) == 1
