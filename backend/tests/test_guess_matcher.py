"""Tests for guess matching.

The cases here are the real ones from Spotify search results, not invented
edge cases. Searching "bohemian rhapsody" against the live API returns the 1975
album cut, a 2023 release, and "Bohemian Rhapsody - Remastered 2011", all under
different track ids. A player picking any of them has identified the song.

The tests are split into two halves that pull against each other: matches that
must be accepted, and near misses that must not be. The second half is the more
important one, since a false positive hands the player a win they did not earn.
"""

import pytest

from app.models.song import ID_TYPE_ISRC, PROVIDER_ISRC, ExternalIdentifier, Song
from app.services.guess_matcher import is_same_song, normalize_artist, normalize_title


def song(song_id: str, title: str, artist: str, isrc: str | None = None) -> Song:
    """Build a song, attaching the ISRC as an external identifier."""
    identifiers = (
        (ExternalIdentifier(PROVIDER_ISRC, ID_TYPE_ISRC, isrc),) if isrc else ()
    )
    return Song(id=song_id, title=title, artist=artist, external_ids=identifiers)


# -- Normalization ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Blinding Lights", "blinding lights"),
        ("Blinding Lights - Single Version", "blinding lights"),
        ("Bohemian Rhapsody - Remastered 2011", "bohemian rhapsody"),
        ("Bohemian Rhapsody (Remastered)", "bohemian rhapsody"),
        ("Dreams - 2004 Remaster", "dreams"),
        ("Mr. Brightside (Album Version)", "mr brightside"),
        ("Levitating (feat. DaBaby)", "levitating"),
        ("Cruel Summer - Deluxe", "cruel summer"),
        ("Don't Stop Me Now", "dont stop me now"),
        ("  Spaced   Out  ", "spaced out"),
    ],
)
def test_normalize_title_strips_release_wording(raw, expected):
    assert normalize_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("The Weeknd", "the weeknd"),
        ("Travis Scott, Skrillex", "travis scott"),
        ("Calvin Harris & Dua Lipa", "calvin harris"),
        ("Jack Ü feat. Justin Bieber", "jack u"),
        ("Beyoncé", "beyonce"),
    ],
)
def test_normalize_artist_reduces_to_the_primary_act(raw, expected):
    assert normalize_artist(raw) == expected


# -- Matches that must be accepted ------------------------------------------


def test_identical_track_ids_match():
    answer = song("abc", "Blinding Lights", "The Weeknd")

    assert is_same_song(song("abc", "Blinding Lights", "The Weeknd"), answer)


def test_same_isrc_under_different_track_ids_matches():
    """The album cut and the single share a master, so they share an ISRC."""
    answer = song("album-cut", "Blinding Lights", "The Weeknd", isrc="USUG11904206")
    guess = song("single", "Blinding Lights", "The Weeknd", isrc="USUG11904206")

    assert is_same_song(guess, answer)


def test_remaster_matches_the_original():
    """The case that prompted this module."""
    answer = song("orig", "Bohemian Rhapsody", "Queen")
    guess = song("remaster", "Bohemian Rhapsody - Remastered 2011", "Queen")

    assert is_same_song(guess, answer)


def test_matching_ignores_a_featured_artist_credit():
    answer = song("a", "Levitating", "Dua Lipa")
    guess = song("b", "Levitating (feat. DaBaby)", "Dua Lipa, DaBaby")

    assert is_same_song(guess, answer)


def test_matching_survives_collaborator_separator_differences():
    answer = song("a", "One Kiss", "Calvin Harris, Dua Lipa")
    guess = song("b", "One Kiss", "Calvin Harris & Dua Lipa")

    assert is_same_song(guess, answer)


# -- Near misses that must be rejected --------------------------------------


def test_same_title_by_a_different_artist_does_not_match():
    """The reason artist has to match, not just title."""
    answer = song("a", "Home", "Edward Sharpe")
    guess = song("b", "Home", "Michael Bublé")

    assert not is_same_song(guess, answer)


def test_different_songs_do_not_match():
    answer = song("a", "Blinding Lights", "The Weeknd")
    guess = song("b", "Save Your Tears", "The Weeknd")

    assert not is_same_song(guess, answer)


def test_differing_isrcs_do_not_short_circuit_to_a_match():
    """A distinct ISRC means a distinct recording, such as a live version."""
    answer = song("a", "Blinding Lights", "The Weeknd", isrc="USUG11904206")
    guess = song("b", "Blinding Lights (Live)", "The Weeknd", isrc="USUG12000001")

    # Falls through to title matching, where "(Live)" is not a release
    # qualifier and so is preserved as a real difference.
    assert not is_same_song(guess, answer)


def test_a_cover_by_another_artist_does_not_match():
    answer = song("a", "Blinding Lights", "The Weeknd")
    guess = song("b", "Blinding Lights", "Teddy Swims")

    assert not is_same_song(guess, answer)


def test_an_empty_title_never_matches():
    """Guards against two unmappable songs normalizing to the same empty string."""
    answer = song("a", "---", "Artist A")
    guess = song("b", "***", "Artist A")

    assert not is_same_song(guess, answer)
