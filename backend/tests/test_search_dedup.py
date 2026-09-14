"""Tests for collapsing search results into one suggestion per song.

The reported bug, from real gameplay. Typing "tek it" offered:

    Tek It              Cafuné
    Tek It - Acoustic   Cafuné
    Tek It - Sped Up    Cafuné
    Tek It              Cafuné      <- the same recording again
    Tek It - Slowed     Cafuné

Two distinct causes. The repeat is one recording released twice and sharing an
ISRC. The versions are genuinely different recordings that simply do not belong
in a guess box when the original is available.

The line these tests hold: collapse releases and suppress versions, but never
lose a song. Two artists with the same song title stay separate, and a song we
only hold as an alternate version stays searchable.
"""

import pytest

from app.models.song import (
    ID_TYPE_ISRC,
    ID_TYPE_TRACK,
    PROVIDER_ISRC,
    PROVIDER_SPOTIFY,
    ExternalIdentifier,
    Song,
)
from app.services.search_dedup import dedupe_for_autocomplete
from app.services.song_versions import canonical_key, is_alternate_version


def song(title, artist, isrc=None, track_id="t"):
    identifiers = [ExternalIdentifier(PROVIDER_SPOTIFY, ID_TYPE_TRACK, track_id)]
    if isrc:
        identifiers.append(ExternalIdentifier(PROVIDER_ISRC, ID_TYPE_ISRC, isrc))
    return Song(id=track_id, title=title, artist=artist, external_ids=tuple(identifiers))


def titles(songs):
    return [item.title for item in songs]


# -- A. The reported case ----------------------------------------------------


def test_the_reported_tek_it_results_collapse_to_one():
    results = dedupe_for_autocomplete(
        [
            song("Tek It", "Cafuné", isrc="QM24S1926168", track_id="a"),
            song("Tek It - Acoustic", "Cafuné", isrc="USEE12200198", track_id="b"),
            song("Tek It - Sped Up", "Cafuné", isrc="USEE12200097", track_id="c"),
            song("Tek It", "Cafuné", isrc="QM24S1926168", track_id="d"),
            song("Tek It - Slowed", "Cafuné", isrc="USEE12200098", track_id="e"),
        ]
    )

    assert titles(results) == ["Tek It"]


@pytest.mark.parametrize(
    "version",
    [
        "Acoustic",
        "Sped Up",
        "Slowed",
        "Nightcore",
        "Live",
        "Remix",
        "Demo",
        "Instrumental",
        "Karaoke",
        "Clean",
        "Radio Edit",
    ],
)
def test_every_named_version_is_suppressed_when_the_original_exists(version):
    results = dedupe_for_autocomplete(
        [
            song("Paper Lantern", "Nova Vale", isrc="AAA", track_id="original"),
            song(f"Paper Lantern - {version}", "Nova Vale", isrc="BBB", track_id="alt"),
        ]
    )

    assert titles(results) == ["Paper Lantern"]


# -- B. Two provider records, one recording ---------------------------------


def test_two_releases_sharing_an_isrc_collapse():
    results = dedupe_for_autocomplete(
        [
            song("Paper Lantern", "Nova Vale", isrc="SHARED1", track_id="single"),
            song("Paper Lantern", "Nova Vale", isrc="SHARED1", track_id="album"),
        ]
    )

    assert len(results) == 1


def test_two_releases_without_an_isrc_still_collapse():
    """Falls back to canonical title and artist when no identifier is shared."""
    results = dedupe_for_autocomplete(
        [
            song("Paper Lantern", "Nova Vale", track_id="one"),
            song("Paper Lantern", "Nova Vale", track_id="two"),
        ]
    )

    assert len(results) == 1


# -- C. Same title, different artists ---------------------------------------


def test_the_same_title_by_different_artists_stays_separate():
    """The reason grouping includes the artist."""
    results = dedupe_for_autocomplete(
        [
            song("Stay", "Artist A", isrc="AAA", track_id="a"),
            song("Stay", "Artist B", isrc="BBB", track_id="b"),
        ]
    )

    assert len(results) == 2
    assert {item.artist for item in results} == {"Artist A", "Artist B"}


def test_a_different_song_with_a_similar_title_survives():
    results = dedupe_for_autocomplete(
        [
            song("Tek It", "Cafuné", isrc="AAA", track_id="a"),
            song("Tek It x Tek It", "sxnata", isrc="BBB", track_id="b"),
            song("Take It", "Staind", isrc="CCC", track_id="c"),
        ]
    )

    assert len(results) == 3


# -- D. Canonical plus live plus remix --------------------------------------


def test_only_the_original_is_offered_when_versions_accompany_it():
    results = dedupe_for_autocomplete(
        [
            song("Paper Lantern", "Nova Vale", isrc="AAA", track_id="a"),
            song("Paper Lantern - Live", "Nova Vale", isrc="BBB", track_id="b"),
            song("Paper Lantern (Kygo Remix)", "Nova Vale", isrc="CCC", track_id="c"),
        ]
    )

    assert titles(results) == ["Paper Lantern"]


def test_the_original_wins_even_when_a_version_ranks_first():
    """Provider relevance order must not decide which release represents a song."""
    results = dedupe_for_autocomplete(
        [
            song("Paper Lantern - Sped Up", "Nova Vale", isrc="BBB", track_id="alt"),
            song("Paper Lantern", "Nova Vale", isrc="AAA", track_id="original"),
        ]
    )

    assert titles(results) == ["Paper Lantern"]


# -- E. Only an alternate version exists ------------------------------------


def test_an_alternate_version_survives_when_it_is_all_we_have():
    """Suppressing it would make a guessable song unguessable."""
    results = dedupe_for_autocomplete(
        [song("Paper Lantern - Acoustic", "Nova Vale", isrc="BBB", track_id="alt")]
    )

    assert titles(results) == ["Paper Lantern - Acoustic"]


def test_several_alternates_with_no_original_all_survive():
    """They are different recordings, so none of them stands in for the others."""
    results = dedupe_for_autocomplete(
        [
            song("Paper Lantern - Acoustic", "Nova Vale", isrc="AAA", track_id="a"),
            song("Paper Lantern - Live", "Nova Vale", isrc="BBB", track_id="b"),
        ]
    )

    assert len(results) == 2


def test_duplicate_alternates_still_collapse_on_isrc():
    results = dedupe_for_autocomplete(
        [
            song("Paper Lantern - Acoustic", "Nova Vale", isrc="SAME", track_id="a"),
            song("Paper Lantern - Acoustic", "Nova Vale", isrc="SAME", track_id="b"),
        ]
    )

    assert len(results) == 1


# -- F. Exact duplicates -----------------------------------------------------


def test_identical_rows_collapse():
    row = song("Paper Lantern", "Nova Vale", isrc="AAA", track_id="a")

    assert len(dedupe_for_autocomplete([row, row, row])) == 1


def test_an_empty_search_stays_empty():
    assert dedupe_for_autocomplete([]) == []


def test_relevance_order_is_preserved():
    results = dedupe_for_autocomplete(
        [
            song("Second", "Artist B", isrc="BBB", track_id="b"),
            song("First", "Artist A", isrc="AAA", track_id="a"),
            song("Third", "Artist C", isrc="CCC", track_id="c"),
        ]
    )

    assert titles(results) == ["Second", "First", "Third"]


# -- Version detection is shared, not scattered -----------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Tek It", False),
        ("Tek It - Acoustic", True),
        ("Tek It - Sped Up", True),
        ("Tek It (Nightcore)", True),
        ("Song (Karaoke)", True),
        ("Song - Clean", True),
        ("Song - Radio Edit", True),
        ("Bohemian Rhapsody - Remastered", False),
    ],
)
def test_alternate_version_detection(title, expected):
    assert is_alternate_version(title) is expected


def test_versions_of_one_song_share_a_canonical_key():
    base = canonical_key("Tek It", "Cafuné")

    assert canonical_key("Tek It - Acoustic", "Cafuné") == base
    assert canonical_key("Tek It - Sped Up", "Cafuné") == base


def test_different_artists_never_share_a_canonical_key():
    assert canonical_key("Stay", "Artist A") != canonical_key("Stay", "Artist B")


# -- The YouTube and popularity pipelines are untouched ---------------------


def test_autocomplete_suppression_does_not_affect_youtube_identity():
    """Presentation-only tokens must not change which recording a video is.

    If "karaoke" or "clean" became identity-affecting, the YouTube matcher would
    start rejecting videos it used to accept, silently changing popularity
    measurements.
    """
    from app.services.song_versions import extract_versions

    for title in ("Song (Karaoke)", "Song - Clean", "Song (Nightcore)"):
        assert extract_versions(title) == frozenset()
        assert is_alternate_version(title) is True


def test_identity_affecting_versions_are_still_identity_affecting():
    from app.services.song_versions import extract_versions

    assert extract_versions("Song - Acoustic") == {"acoustic"}
    assert extract_versions("Song (Live)") == {"live"}
    assert extract_versions("Song (Kygo Remix)") == {"remix"}
