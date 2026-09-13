"""Regression tests for version qualifier matching.

The bug these cover, found during the 89 song calibration:

    catalog title   "Jasmine - Demo"
    video title     "Jai Paul - Jasmine (Demo)"

The matcher stripped brackets from the video title to remove wording like
"(Official Video)", which also removed "(Demo)". The catalog title kept its
qualifier, so the two never matched and a correct video was rejected.

The fix compares version qualifiers on both sides before comparing titles. The
line it must hold: a demo matches a demo, and a demo never matches the studio
recording. Loosening this to make more songs match would let a remix or a live
take stand in for the studio version, and those have different audiences and
very different view counts.
"""

import pytest

from app.catalog.youtube_matcher import (
    VideoMatchConfidence,
    extract_versions,
    score_candidate,
    strip_upload_noise,
)
from app.providers.youtube.youtube_provider import YouTubeVideo


def video(title, channel="Jai Paul", duration_ms=253_000, views=5_000_000):
    return YouTubeVideo(
        video_id="v",
        title=title,
        channel_id="c",
        channel_title=channel,
        duration_ms=duration_ms,
        view_count=views,
    )


# -- Version extraction ------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Jasmine - Demo", {"demo"}),
        ("Jai Paul - Jasmine (Demo)", {"demo"}),
        ("Blinding Lights", set()),
        ("The Weeknd - Blinding Lights (Official Video)", set()),
        ("Cherry Wine - Live", {"live"}),
        ("Hozier - Cherry Wine (Live)", {"live"}),
        ("Song - Acoustic", {"acoustic"}),
        ("Song - 2011 Remaster", {"remaster"}),
        ("Song (Radio Edit)", {"radio_edit"}),
        ("BTSTU - Edit", {"edit"}),
    ],
)
def test_version_extraction(title, expected):
    assert extract_versions(title) == expected


def test_a_remix_is_not_also_reported_as_a_mix():
    """"remix" contains "mix", so a substring check would report both."""
    assert extract_versions("Song (Kygo Remix)") == {"remix"}


def test_a_radio_edit_is_not_also_reported_as_an_edit():
    assert extract_versions("Song (Radio Edit)") == {"radio_edit"}


def test_upload_decoration_is_not_mistaken_for_a_version():
    for noise in ("(Official Video)", "[Official Audio]", "(Lyrics)", "4K", "(HD)"):
        assert extract_versions(f"Artist - Song {noise}") == set()


# -- Noise stripping keeps identity ------------------------------------------


def test_stripping_noise_keeps_version_wording():
    """The heart of the bug. "(Official Video)" is noise, "(Demo)" is identity."""
    cleaned = strip_upload_noise("Jai Paul - Jasmine (Demo) (Official Video)")

    assert "demo" in cleaned
    assert "official" not in cleaned


# -- The regression itself ---------------------------------------------------


def test_a_demo_matches_the_demo_video():
    """The exact case that failed during calibration."""
    match = score_candidate(
        video("Jai Paul - Jasmine (Demo)"), "Jasmine - Demo", "Jai Paul", 253_080
    )

    assert match.accepted
    assert match.confidence is VideoMatchConfidence.VERIFIED_OFFICIAL


def test_an_edit_matches_the_edit_video():
    match = score_candidate(
        video("Jai Paul - BTSTU (Edit)", duration_ms=209_000),
        "BTSTU - Edit",
        "Jai Paul",
        209_493,
    )

    assert match.accepted


# -- And must not have loosened anything -------------------------------------


def test_a_demo_does_not_match_the_studio_recording():
    """A demo is a different recording with a different audience."""
    match = score_candidate(
        video("Jai Paul - Jasmine"), "Jasmine - Demo", "Jai Paul", 253_080
    )

    assert not match.accepted
    assert "version differs" in match.reason


def test_the_studio_recording_does_not_match_a_demo_video():
    match = score_candidate(
        video("Jai Paul - Jasmine (Demo)"), "Jasmine", "Jai Paul", 253_080
    )

    assert not match.accepted


def test_a_remix_still_never_matches_the_original():
    match = score_candidate(
        video("Blinding Lights (Chromatics Remix)", channel="TheWeekndVEVO"),
        "Blinding Lights",
        "The Weeknd",
        200_040,
    )

    assert not match.accepted


def test_a_live_video_still_never_matches_a_studio_song():
    match = score_candidate(
        video("Blinding Lights (Live at the BRITs)", channel="TheWeekndVEVO"),
        "Blinding Lights",
        "The Weeknd",
        200_040,
    )

    assert not match.accepted


def test_an_acoustic_version_does_not_match_the_studio_song():
    match = score_candidate(
        video("Song - Acoustic", channel="Artist"), "Song", "Artist", 200_000
    )

    assert not match.accepted


def test_a_remaster_does_not_stand_in_for_the_original_here():
    """The catalog resolver is generous about remasters, this is not.

    For popularity the remaster and the original are separate uploads with
    separate view counts, so mixing them would double count or undercount.
    """
    match = score_candidate(
        video("Bohemian Rhapsody (2011 Remaster)", channel="Queen Official"),
        "Bohemian Rhapsody",
        "Queen",
        355_000,
    )

    assert not match.accepted


def test_the_ordinary_case_still_works():
    match = score_candidate(
        video(
            "The Weeknd - Blinding Lights (Official Video)",
            channel="TheWeekndVEVO",
            duration_ms=262_000,
        ),
        "Blinding Lights",
        "The Weeknd",
        200_040,
    )

    assert match.confidence is VideoMatchConfidence.VERIFIED_OFFICIAL
