"""Tests for catalog identity resolution.

The bias here is the opposite of the guess matcher's. A guess matcher false
negative annoys one player once. A resolver false positive merges two different
recordings into one catalog row, which is silent, persistent, and hard to undo.
So most of these tests are about refusing to match.
"""

import pytest

from app.catalog.resolver import (
    Candidate,
    ExistingSong,
    MatchConfidence,
    best_match,
    durations_agree,
    resolve_match,
)


def candidate(**overrides) -> Candidate:
    base = {
        "provider": "spotify",
        "external_id": "sp-1",
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "isrcs": (),
        "duration_ms": 200_040,
        "musicbrainz_id": None,
    }
    return Candidate(**{**base, **overrides})


def existing(**overrides) -> ExistingSong:
    base = {
        "song_id": "audidle-1",
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "isrcs": (),
        "duration_ms": 200_040,
        "musicbrainz_id": None,
        "provider_ids": {},
    }
    return ExistingSong(**{**base, **overrides})


# -- Accepted matches --------------------------------------------------------


def test_a_known_provider_id_is_certain():
    result = resolve_match(
        candidate(external_id="sp-1"), existing(provider_ids={"spotify": "sp-1"})
    )

    assert result.confidence is MatchConfidence.EXACT_PROVIDER_ID
    assert result.accepted


def test_a_shared_isrc_matches():
    """The strongest cross-provider evidence available."""
    result = resolve_match(
        candidate(isrcs=("USUG11904206",)), existing(isrcs=("USUG11904206",))
    )

    assert result.confidence is MatchConfidence.EXACT_ISRC


def test_one_shared_isrc_is_enough_when_a_song_has_several():
    result = resolve_match(
        candidate(isrcs=("GBAHS1600463", "USUG11904206")),
        existing(isrcs=("USUG11904206",)),
    )

    assert result.confidence is MatchConfidence.EXACT_ISRC


def test_a_shared_mbid_matches():
    result = resolve_match(
        candidate(musicbrainz_id="mbid-1"), existing(musicbrainz_id="mbid-1")
    )

    assert result.confidence is MatchConfidence.EXACT_MBID


def test_metadata_agreement_matches_when_nothing_stronger_exists():
    result = resolve_match(candidate(), existing())

    assert result.confidence is MatchConfidence.HIGH_CONFIDENCE_METADATA


def test_metadata_matching_tolerates_release_wording():
    result = resolve_match(
        candidate(title="Blinding Lights - Single Version"), existing()
    )

    assert result.confidence is MatchConfidence.HIGH_CONFIDENCE_METADATA


def test_a_missing_duration_does_not_block_a_metadata_match():
    """Plenty of entries have no duration, and rejecting those loses good matches."""
    result = resolve_match(candidate(duration_ms=None), existing())

    assert result.accepted


# -- Refused matches ---------------------------------------------------------


def test_differing_isrcs_on_both_sides_refuse_to_match():
    """The real case that motivated this module.

    MusicBrainz's top scored "Blinding Lights" carries USUMV2403154 while
    Spotify's carries USUG11904206. Same title, same artist, different
    recordings. Two known and different ISRCs is positive evidence of a
    difference, so metadata agreement must not override it.
    """
    result = resolve_match(
        candidate(isrcs=("USUMV2403154",)), existing(isrcs=("USUG11904206",))
    )

    assert result.confidence is MatchConfidence.UNRESOLVED
    assert not result.accepted
    assert "none match" in result.reason


def test_a_title_match_alone_is_never_accepted():
    result = resolve_match(candidate(artist="Teddy Swims"), existing())

    assert result.confidence is MatchConfidence.UNRESOLVED
    assert "artist differs" in result.reason


def test_a_large_duration_difference_refuses_to_match():
    """Separates a radio edit or an extended mix from the album cut."""
    result = resolve_match(candidate(duration_ms=200_000), existing(duration_ms=320_000))

    assert result.confidence is MatchConfidence.UNRESOLVED
    assert "durations differ" in result.reason


def test_a_different_song_does_not_match():
    result = resolve_match(candidate(title="Save Your Tears"), existing())

    assert result.confidence is MatchConfidence.UNRESOLVED


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (200_000, 200_000, True),
        (200_000, 202_000, True),
        (200_000, 203_000, True),
        (200_000, 210_000, False),
        (None, 200_000, True),
        (200_000, None, True),
        (None, None, True),
    ],
)
def test_duration_tolerance(left, right, expected):
    assert durations_agree(left, right) is expected


# -- Choosing among several --------------------------------------------------


def test_the_strongest_match_wins():
    weak = existing(song_id="weak")
    strong = existing(song_id="strong", isrcs=("USUG11904206",))

    song, result = best_match(candidate(isrcs=("USUG11904206",)), [weak, strong])

    assert song is not None
    assert song.song_id == "strong"
    assert result.confidence is MatchConfidence.EXACT_ISRC


def test_ambiguity_is_reported_rather_than_broken_by_a_tiebreak():
    """Two equally strong matches means the catalog already holds a duplicate.

    Picking one arbitrarily would compound it, so this refuses instead.
    """
    first = existing(song_id="a")
    second = existing(song_id="b")

    song, result = best_match(candidate(), [first, second])

    assert song is None
    assert result.confidence is MatchConfidence.UNRESOLVED
    assert "ambiguous" in result.reason


def test_no_existing_songs_is_a_clean_miss():
    song, result = best_match(candidate(), [])

    assert song is None
    assert result.confidence is MatchConfidence.UNRESOLVED


def test_only_weak_candidates_returns_an_explanation():
    song, result = best_match(candidate(artist="Someone Else"), [existing()])

    assert song is None
    assert not result.accepted
    assert result.reason


def test_confidence_levels_know_whether_they_are_acceptable():
    assert MatchConfidence.EXACT_ISRC.is_acceptable
    assert MatchConfidence.HIGH_CONFIDENCE_METADATA.is_acceptable
    assert MatchConfidence.MANUAL.is_acceptable
    assert not MatchConfidence.UNRESOLVED.is_acceptable
