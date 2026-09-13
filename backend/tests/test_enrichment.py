"""Tests for the enrichment passes.

Providers are stubbed, so these run offline. Two properties get most of the
attention because both are about not losing work:

**Resumability.** At one MusicBrainz request per second, a few thousand songs is
hours, and the run will be interrupted. Rerunning must pick up where it stopped
rather than starting over or duplicating.

**Failure isolation.** A provider error on one song must not destroy that song's
existing data or stop the run.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog.enrich import (
    enrich_with_musicbrainz,
    enrich_with_spotify,
    recompute_all_eligibility,
)
from app.catalog.writer import attach_identifier, attach_isrc, upsert_song
from app.core.errors import CatalogUnavailableError
from app.database.catalog_models import (
    CatalogSong,
    ExternalIdentifierRow,
    Genre,
    SongGenre,
)
from app.database.models import Base
from app.models.song import (
    ID_TYPE_RECORDING_MBID,
    ID_TYPE_TRACK,
    PROVIDER_MUSICBRAINZ,
    PROVIDER_SPOTIFY,
)
from app.models.song import Song as DomainSong
from app.providers.musicbrainz.musicbrainz_provider import (
    MusicBrainzArtist,
    MusicBrainzRecording,
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


def recording(**overrides) -> MusicBrainzRecording:
    base = {
        "mbid": "mbid-1",
        "title": "Blinding Lights",
        "artist_credit": "The Weeknd",
        "artists": (MusicBrainzArtist(mbid="artist-1", name="The Weeknd"),),
        "isrcs": ("USUG11904206",),
        "length_ms": 200_040,
        "first_release_year": 2019,
        "tags": ("synthpop", "pop"),
    }
    return MusicBrainzRecording(**{**base, **overrides})


class StubMusicBrainz:
    """A MusicBrainz provider that serves canned recordings and counts calls."""

    def __init__(self, by_isrc=None, by_search=None, by_mbid=None, fail: bool = False):
        self.by_isrc = by_isrc or {}
        self.by_search = by_search if by_search is not None else []
        self.by_mbid = by_mbid or {}
        self.fail = fail
        self.calls = 0

    async def get_recording_by_isrc(self, isrc: str):
        self.calls += 1
        if self.fail:
            raise CatalogUnavailableError("MusicBrainz is down")
        return self.by_isrc.get(isrc.upper())

    async def get_recording(self, mbid: str):
        self.calls += 1
        if self.fail:
            raise CatalogUnavailableError("MusicBrainz is down")
        return self.by_mbid.get(mbid)

    async def search_recordings(self, title, artist=None, limit=10):
        self.calls += 1
        if self.fail:
            raise CatalogUnavailableError("MusicBrainz is down")
        return list(self.by_search)


class StubSpotify:
    """Returns canned search results, keyed by query prefix."""

    def __init__(self, results: dict[str, list[DomainSong]] | None = None):
        self.results = results or {}
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 10):
        self.queries.append(query)
        for prefix, found in self.results.items():
            if query.startswith(prefix):
                return found[:limit]
        return []


async def make_song(session, title="Blinding Lights", artist="The Weeknd", **kwargs):
    return await upsert_song(
        session, song_id=None, title=title, artist_credit=artist, **kwargs
    )


async def count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# -- MusicBrainz enrichment --------------------------------------------------


@pytest.mark.anyio
async def test_isrc_lookup_is_preferred_over_search(session):
    """Exact beats a title search, and costs fewer requests."""
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")

    provider = StubMusicBrainz(
        by_isrc={"USUG11904206": recording()}, by_mbid={"mbid-1": recording()}
    )
    report = await enrich_with_musicbrainz(session, provider)

    assert report.matched == 1
    # The ISRC lookup plus the tag re-fetch. No search was needed.
    assert provider.by_search == []


@pytest.mark.anyio
async def test_enrichment_attaches_every_isrc_the_recording_has(session):
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")

    multi = recording(isrcs=("USUG11904206", "GBAHS1600463", "DEUM71807062"))
    provider = StubMusicBrainz(by_isrc={"USUG11904206": multi}, by_mbid={"mbid-1": multi})
    await enrich_with_musicbrainz(session, provider)

    isrcs = (
        await session.execute(
            select(ExternalIdentifierRow).where(
                ExternalIdentifierRow.song_id == song.id,
                ExternalIdentifierRow.identifier_type == "isrc",
            )
        )
    ).scalars().all()
    assert len(isrcs) == 3


@pytest.mark.anyio
async def test_tags_become_genres(session):
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")

    provider = StubMusicBrainz(
        by_isrc={"USUG11904206": recording()}, by_mbid={"mbid-1": recording()}
    )
    await enrich_with_musicbrainz(session, provider)

    assert await count(session, SongGenre) == 2
    slugs = {row.slug for row in (await session.execute(select(Genre))).scalars()}
    assert slugs == {"synthpop", "pop"}


@pytest.mark.anyio
async def test_a_recording_with_no_tags_adds_no_genres(session):
    """Missing genre data must not be invented."""
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")

    bare = recording(tags=())
    provider = StubMusicBrainz(by_isrc={"USUG11904206": bare}, by_mbid={"mbid-1": bare})
    await enrich_with_musicbrainz(session, provider)

    assert await count(session, Genre) == 0


@pytest.mark.anyio
async def test_a_weak_search_match_is_refused_and_parked(session):
    """Same title, different artist. Never accepted automatically."""
    await make_song(session, title="Blinding Lights", artist="Teddy Swims")

    provider = StubMusicBrainz(by_search=[recording()])
    report = await enrich_with_musicbrainz(session, provider)

    assert report.matched == 0
    assert report.unresolved == 1
    assert await count(session, ExternalIdentifierRow) == 0


@pytest.mark.anyio
async def test_several_plausible_recordings_are_refused_as_ambiguous(session):
    await make_song(session)

    provider = StubMusicBrainz(
        by_search=[recording(mbid="a", isrcs=()), recording(mbid="b", isrcs=())]
    )
    report = await enrich_with_musicbrainz(session, provider)

    assert report.matched == 0
    assert report.unresolved == 1
    assert "ambiguous" in report.reasons[0]


@pytest.mark.anyio
async def test_enrichment_fills_gaps_without_overwriting(session):
    """A pass that knows less must not erase what we already had."""
    song = await make_song(session, release_year=2020, duration_ms=123_456)
    await attach_isrc(session, song, "USUG11904206")

    provider = StubMusicBrainz(
        by_isrc={"USUG11904206": recording()}, by_mbid={"mbid-1": recording()}
    )
    await enrich_with_musicbrainz(session, provider)

    refreshed = await session.get(CatalogSong, song.id)
    assert refreshed.release_year == 2020
    assert refreshed.duration_ms == 123_456


# -- Resumability ------------------------------------------------------------


@pytest.mark.anyio
async def test_a_second_run_skips_songs_already_enriched(session):
    """The property that makes a multi hour import survive interruption."""
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")

    provider = StubMusicBrainz(
        by_isrc={"USUG11904206": recording()}, by_mbid={"mbid-1": recording()}
    )
    await enrich_with_musicbrainz(session, provider)
    calls_after_first = provider.calls

    second = await enrich_with_musicbrainz(session, provider)

    assert second.skipped_already_done == 1
    assert second.matched == 0
    # Not a single extra request was spent.
    assert provider.calls == calls_after_first


@pytest.mark.anyio
async def test_a_partial_run_continues_from_where_it_stopped(session):
    """Three songs, one already done. Only the other two are worked on."""
    for index in range(3):
        song = await make_song(session, title=f"Song {index}")
        await attach_isrc(session, song, f"ISRC{index:08d}")
        if index == 0:
            await attach_identifier(
                session, song, PROVIDER_MUSICBRAINZ, ID_TYPE_RECORDING_MBID, "done"
            )

    provider = StubMusicBrainz(
        by_isrc={
            "ISRC00000001": recording(mbid="m1"),
            "ISRC00000002": recording(mbid="m2"),
        },
        by_mbid={"m1": recording(mbid="m1"), "m2": recording(mbid="m2")},
    )
    report = await enrich_with_musicbrainz(session, provider)

    assert report.skipped_already_done == 1
    assert report.matched == 2


@pytest.mark.anyio
async def test_force_re_enriches_songs_that_were_already_done(session):
    """For backfilling data a newer version of the pass collects."""
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")
    await attach_identifier(
        session, song, PROVIDER_MUSICBRAINZ, ID_TYPE_RECORDING_MBID, "mbid-1"
    )

    provider = StubMusicBrainz(
        by_isrc={"USUG11904206": recording()}, by_mbid={"mbid-1": recording()}
    )
    report = await enrich_with_musicbrainz(session, provider, force=True)

    assert report.skipped_already_done == 0
    assert report.matched == 1


# -- Failure isolation -------------------------------------------------------


@pytest.mark.anyio
async def test_a_provider_outage_does_not_destroy_existing_data(session):
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")

    report = await enrich_with_musicbrainz(session, StubMusicBrainz(fail=True))

    assert report.failed == 1
    assert report.matched == 0
    # The song and its ISRC are untouched.
    refreshed = await session.get(CatalogSong, song.id)
    assert refreshed.title == "Blinding Lights"
    assert await count(session, ExternalIdentifierRow) == 1


@pytest.mark.anyio
async def test_one_failure_does_not_stop_the_run(session):
    """A single bad song must not cost the rest of a multi hour import."""
    for index in range(3):
        current = await make_song(session, title=f"Song {index}")
        await attach_isrc(session, current, f"ISRC{index:08d}")

    class FlakyProvider(StubMusicBrainz):
        async def get_recording_by_isrc(self, isrc: str):
            if isrc == "ISRC00000001":
                raise CatalogUnavailableError("transient")
            return self.by_isrc.get(isrc.upper())

    provider = FlakyProvider(
        by_isrc={
            "ISRC00000000": recording(mbid="m0"),
            "ISRC00000002": recording(mbid="m2"),
        },
        by_mbid={"m0": recording(mbid="m0"), "m2": recording(mbid="m2")},
    )
    report = await enrich_with_musicbrainz(session, provider)

    assert report.failed == 1
    assert report.matched == 2


# -- Spotify enrichment ------------------------------------------------------


@pytest.mark.anyio
async def test_spotify_matches_through_isrc(session):
    """The reason ISRCs are worth collecting."""
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")

    match = DomainSong(
        id="spotify-track-1",
        title="Blinding Lights",
        artist="The Weeknd",
        artwork_url="https://example.test/art.jpg",
    )
    provider = StubSpotify({"isrc:USUG11904206": [match]})
    report = await enrich_with_spotify(session, provider)

    assert report.matched == 1
    assert provider.queries[0] == "isrc:USUG11904206"

    attached = (
        await session.execute(
            select(ExternalIdentifierRow).where(
                ExternalIdentifierRow.provider == PROVIDER_SPOTIFY,
                ExternalIdentifierRow.identifier_type == ID_TYPE_TRACK,
            )
        )
    ).scalars().first()
    assert attached.identifier == "spotify-track-1"
    assert attached.confidence == "exact_isrc"


@pytest.mark.anyio
async def test_spotify_enrichment_fills_in_artwork(session):
    song = await make_song(session)
    await attach_isrc(session, song, "USUG11904206")

    match = DomainSong(
        id="sp-1",
        title="Blinding Lights",
        artist="The Weeknd",
        artwork_url="https://example.test/art.jpg",
        album="After Hours",
    )
    await enrich_with_spotify(session, StubSpotify({"isrc:": [match]}))

    refreshed = await session.get(CatalogSong, song.id)
    assert refreshed.artwork_url == "https://example.test/art.jpg"
    assert refreshed.album == "After Hours"


@pytest.mark.anyio
async def test_spotify_falls_back_to_metadata_when_there_is_no_isrc(session):
    await make_song(session, duration_ms=200_000)

    match = DomainSong(
        id="sp-1", title="Blinding Lights", artist="The Weeknd", duration_ms=200_500
    )
    provider = StubSpotify({"track:": [match]})
    report = await enrich_with_spotify(session, provider)

    assert report.matched == 1
    assert provider.queries[0].startswith("track:")


@pytest.mark.anyio
async def test_spotify_refuses_a_wrong_artist(session):
    await make_song(session)

    match = DomainSong(id="sp-1", title="Blinding Lights", artist="Teddy Swims")
    report = await enrich_with_spotify(session, StubSpotify({"track:": [match]}))

    assert report.matched == 0
    assert report.unresolved == 1


# -- Eligibility recompute ---------------------------------------------------


@pytest.mark.anyio
async def test_recompute_covers_the_whole_catalog(session):
    for index in range(3):
        await make_song(session, title=f"Song {index}")

    counts = await recompute_all_eligibility(session)

    assert counts["total"] == 3
    # None have audio or popularity, so none are eligible.
    assert counts["eligible"] == 0
