"""Tests for catalog expansion.

The properties that matter: a new song may arrive with no popularity at all and
must stay out of the game pool until it has some, deduplication runs strongest
key first, and a batch stops at its target instead of importing everything the
discovery source happened to return.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog.expand import find_existing, import_candidates
from app.catalog.writer import attach_identifier, attach_isrc, upsert_song
from app.database.catalog_models import CatalogSong, SongEligibility, SongPopularityRow
from app.database.models import Base
from app.models.song import ID_TYPE_RECORDING_MBID, PROVIDER_MUSICBRAINZ
from app.providers.seed.base import SongCandidate


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        yield active
    await engine.dispose()


def candidate(**overrides) -> SongCandidate:
    base = {
        "title": "Paper Lantern",
        "artist": "Nova Vale",
        "musicbrainz_recording_id": "mbid-1",
        "source": "listenbrainz-artist",
        "source_id": "mbid-1",
    }
    return SongCandidate(**{**base, **overrides})


async def count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# -- A song may exist without popularity ------------------------------------


@pytest.mark.anyio
async def test_a_new_song_is_created_without_popularity(session):
    report = await import_candidates(session, [candidate()])

    assert report.created == 1
    assert await count(session, SongPopularityRow) == 0


@pytest.mark.anyio
async def test_a_song_without_popularity_is_not_game_eligible(session):
    """Knowing about a song is not the same as being able to use it."""
    await import_candidates(session, [candidate()])

    record = (await session.execute(select(SongEligibility))).scalars().first()

    assert record.eligible is False
    assert record.difficulty is None
    assert record.has_difficulty is False


@pytest.mark.anyio
async def test_no_stream_count_is_invented(session):
    await import_candidates(session, [candidate()])

    rows = (await session.execute(select(SongPopularityRow))).scalars().all()

    assert rows == []


# -- Deduplication, strongest key first -------------------------------------


@pytest.mark.anyio
async def test_a_repeat_import_creates_nothing(session):
    await import_candidates(session, [candidate()])
    second = await import_candidates(session, [candidate()])

    assert second.created == 0
    assert await count(session, CatalogSong) == 1


@pytest.mark.anyio
async def test_a_matching_mbid_is_recognised(session):
    song = await upsert_song(
        session, song_id=None, title="Paper Lantern", artist_credit="Nova Vale"
    )
    await attach_identifier(
        session, song, PROVIDER_MUSICBRAINZ, ID_TYPE_RECORDING_MBID, "mbid-1"
    )

    found, how = await find_existing(session, candidate(source_id=None))

    assert found is not None
    assert how == "mbid"


@pytest.mark.anyio
async def test_a_matching_isrc_is_recognised(session):
    song = await upsert_song(
        session, song_id=None, title="Something Else", artist_credit="Nova Vale"
    )
    await attach_isrc(session, song, "AAA111111111")

    found, how = await find_existing(
        session, candidate(isrc="AAA111111111", source_id=None, musicbrainz_recording_id=None)
    )

    assert found is not None
    assert how == "isrc"


@pytest.mark.anyio
async def test_title_and_artist_are_only_a_fallback(session):
    await upsert_song(
        session, song_id=None, title="Paper Lantern", artist_credit="Nova Vale"
    )

    found, how = await find_existing(
        session, candidate(source_id=None, musicbrainz_recording_id=None)
    )

    assert found is not None
    assert how == "metadata"


@pytest.mark.anyio
async def test_the_same_title_by_another_artist_is_a_different_song(session):
    await import_candidates(session, [candidate(artist="Nova Vale")])
    report = await import_candidates(
        session,
        [candidate(artist="Other Act", musicbrainz_recording_id="mbid-2", source_id="mbid-2")],
    )

    assert report.created == 1
    assert await count(session, CatalogSong) == 2


@pytest.mark.anyio
async def test_an_alternate_version_is_kept_as_its_own_song(session):
    """Storage and presentation want different answers here.

    Autocomplete folds "Song - Acoustic" into "Song" so a player sees one
    suggestion. The catalog must not: an acoustic version is a different
    recording, and collapsing it would lose a song we legitimately hold.
    """
    await import_candidates(session, [candidate()])

    report = await import_candidates(
        session,
        [
            candidate(
                title="Paper Lantern - Acoustic",
                musicbrainz_recording_id="mbid-acoustic",
                source_id="mbid-acoustic",
            )
        ],
    )

    assert report.created == 1
    assert await count(session, CatalogSong) == 2


@pytest.mark.anyio
async def test_a_remaster_is_still_the_same_recording(session):
    """Pure release wording is folded, unlike a genuine version difference."""
    await import_candidates(session, [candidate()])

    found, how = await find_existing(
        session,
        candidate(
            title="Paper Lantern - 2011 Remaster",
            musicbrainz_recording_id=None,
            source_id=None,
        ),
    )

    assert found is not None
    assert how == "metadata"


# -- Batch size --------------------------------------------------------------


@pytest.mark.anyio
async def test_a_batch_stops_at_its_target(session):
    """Callers over-discover, so the cap is what keeps a batch to its size."""
    candidates = [
        candidate(
            title=f"Song {index}",
            musicbrainz_recording_id=f"m{index}",
            source_id=f"m{index}",
        )
        for index in range(20)
    ]

    report = await import_candidates(session, candidates, max_new=5)

    assert report.created == 5
    assert await count(session, CatalogSong) == 5


@pytest.mark.anyio
async def test_duplicates_do_not_consume_the_batch_allowance(session):
    await import_candidates(session, [candidate()])

    candidates = [candidate()] + [
        candidate(
            title=f"New {index}",
            musicbrainz_recording_id=f"n{index}",
            source_id=f"n{index}",
        )
        for index in range(5)
    ]
    report = await import_candidates(session, candidates, max_new=3)

    assert report.created == 3
    assert report.duplicates == 1


@pytest.mark.anyio
async def test_a_failing_candidate_does_not_stop_the_batch(session):
    candidates = [
        candidate(title="", musicbrainz_recording_id="bad", source_id="bad"),
        candidate(title="Good", musicbrainz_recording_id="good", source_id="good"),
    ]

    report = await import_candidates(session, candidates)

    assert report.created >= 1
