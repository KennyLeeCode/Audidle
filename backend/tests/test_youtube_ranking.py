"""Choosing the best popularity measurement among valid candidates.

Two separate questions, and conflating them caused a real failure:

  1. Is this the correct recording?
  2. Is this the best valid upload to measure popularity from?

A Topic-channel upload can answer yes to the first and still be a poor answer to
the second. In the calibration set a song matched a 1.4M view Topic upload while
its official video, the same recording, had roughly a thousand times the
audience. Identity was right and the measurement was meaningless.

The rule these tests pin down: identity is settled first and never traded away
for popularity. Only among candidates already accepted as the right recording
does view count decide which one represents the song.

Written against generic artists so no single song is special-cased.
"""

import pytest

from app.catalog.youtube_matcher import VideoMatchConfidence, pick_best_video, score_candidate
from app.providers.youtube.youtube_provider import YouTubeVideo

ARTIST = "Nova Vale"
TITLE = "Paper Lantern"
SONG_MS = 240_000


def upload(channel, views, title=None, duration_ms=SONG_MS, video_id=None):
    return YouTubeVideo(
        video_id=video_id or f"{channel[:6]}-{views}",
        title=title or f"{ARTIST} - {TITLE}",
        channel_id="c",
        channel_title=channel,
        duration_ms=duration_ms,
        view_count=views,
    )


def choose(*videos):
    return pick_best_video(list(videos), TITLE, ARTIST, SONG_MS)


# -- A. A far bigger official upload beats an authoritative small one --------


def test_a_vevo_upload_beats_a_tiny_topic_upload():
    """Both are valid. The one with the audience is the better measurement."""
    topic = upload(f"{ARTIST} - Topic", 1_400_000, video_id="topic")
    vevo = upload(
        f"{ARTIST}VEVO",
        1_500_000_000,
        title=f"{ARTIST} - {TITLE} (Official Video)",
        video_id="vevo",
    )

    chosen = choose(topic, vevo)

    assert chosen is not None
    assert chosen.video.video_id == "vevo"


def test_the_order_candidates_arrive_in_does_not_matter():
    topic = upload(f"{ARTIST} - Topic", 1_400_000, video_id="topic")
    vevo = upload(
        f"{ARTIST}VEVO",
        1_500_000_000,
        title=f"{ARTIST} - {TITLE} (Official Video)",
        video_id="vevo",
    )

    assert choose(vevo, topic).video.video_id == "vevo"
    assert choose(topic, vevo).video.video_id == "vevo"


# -- B. Popularity never overrides identity ---------------------------------


def test_a_huge_live_performance_loses_to_a_small_valid_studio_upload():
    """The rule that keeps this honest.

    A 500M view live performance is a different recording with a different
    audience. It must never stand in for the studio version, however popular.
    """
    studio = upload(f"{ARTIST} - Topic", 10_000_000, video_id="studio")
    live = upload(
        f"{ARTIST}VEVO",
        500_000_000,
        title=f"{ARTIST} - {TITLE} (Live at Glastonbury)",
        video_id="live",
    )

    chosen = choose(studio, live)

    assert chosen is not None
    assert chosen.video.video_id == "studio"


def test_a_huge_remix_loses_to_a_small_valid_studio_upload():
    studio = upload(f"{ARTIST} - Topic", 8_000_000, video_id="studio")
    remix = upload(
        f"{ARTIST}VEVO",
        900_000_000,
        title=f"{ARTIST} - {TITLE} (Kygo Remix)",
        video_id="remix",
    )

    assert choose(studio, remix).video.video_id == "studio"


def test_a_huge_upload_by_the_wrong_artist_loses():
    studio = upload(f"{ARTIST} - Topic", 5_000_000, video_id="studio")
    cover = upload(
        "Someone Else",
        800_000_000,
        title=f"Someone Else - {TITLE}",
        video_id="cover",
    )

    assert choose(studio, cover).video.video_id == "studio"


def test_a_huge_upload_of_the_wrong_length_loses():
    """Catches extended mixes and hour long loops that keep the right title."""
    studio = upload(f"{ARTIST} - Topic", 5_000_000, video_id="studio")
    extended = upload(
        f"{ARTIST}VEVO",
        700_000_000,
        duration_ms=3_600_000,
        video_id="extended",
    )

    assert choose(studio, extended).video.video_id == "studio"


# -- C. A Topic upload on its own is still accepted -------------------------


def test_a_lone_topic_upload_is_accepted():
    """Topic channels are not penalized. They are often the only official one."""
    topic = upload(f"{ARTIST} - Topic", 120_000, video_id="topic")

    chosen = choose(topic)

    assert chosen is not None
    assert chosen.video.video_id == "topic"
    assert chosen.confidence is VideoMatchConfidence.VERIFIED_OFFICIAL


def test_a_lone_topic_upload_with_very_few_views_is_still_accepted():
    """A small audience is a measurement, not a reason to reject the match."""
    chosen = choose(upload(f"{ARTIST} - Topic", 535, video_id="topic"))

    assert chosen is not None
    assert chosen.video.video_id == "topic"


def test_a_topic_upload_beats_a_non_official_channel():
    topic = upload(f"{ARTIST} - Topic", 200_000, video_id="topic")
    reupload = upload("Indie Music Archive", 2_000_000, video_id="reupload")

    assert choose(topic, reupload).video.video_id == "topic"


# -- D. Two valid authoritative uploads of the same recording ---------------


def test_the_better_represented_of_two_official_uploads_wins():
    """A single usually has both an official video and an official audio."""
    audio = upload(
        f"{ARTIST}VEVO",
        878_000_000,
        title=f"{ARTIST} - {TITLE} (Official Audio)",
        video_id="audio",
    )
    music_video = upload(
        f"{ARTIST}VEVO",
        1_063_000_000,
        title=f"{ARTIST} - {TITLE} (Official Video)",
        duration_ms=262_000,
        video_id="video",
    )

    assert choose(audio, music_video).video.video_id == "video"


def test_authority_still_leads_when_audiences_are_comparable():
    """Seniority decides when neither upload dominates.

    The dominance rule is for order-of-magnitude gaps, not for preferring
    whichever official upload happens to be slightly ahead.
    """
    artist_channel = upload(ARTIST, 40_000_000, video_id="artist")
    topic = upload(f"{ARTIST} - Topic", 55_000_000, video_id="topic")

    assert choose(artist_channel, topic).video.video_id == "artist"


def test_a_thousandfold_gap_overrides_authority():
    artist_channel = upload(ARTIST, 100_000, video_id="artist")
    topic = upload(f"{ARTIST} - Topic", 500_000_000, video_id="topic")

    assert choose(artist_channel, topic).video.video_id == "topic"


# -- Nothing was loosened ---------------------------------------------------


def test_no_valid_candidate_still_returns_nothing():
    """Popularity ranking must not rescue a set with no valid recording in it."""
    live = upload(f"{ARTIST}VEVO", 900_000_000, title=f"{ARTIST} - {TITLE} (Live)")
    karaoke = upload("Karaoke Co", 40_000_000, title=f"{TITLE} karaoke")

    assert choose(live, karaoke) is None


def test_missing_view_counts_do_not_crash_the_ranking():
    """Uploaders can hide the count, which is a normal state."""
    known = upload(f"{ARTIST} - Topic", 5_000, video_id="known")
    hidden = YouTubeVideo(
        video_id="hidden",
        title=f"{ARTIST} - {TITLE}",
        channel_id="c",
        channel_title=f"{ARTIST}VEVO",
        duration_ms=SONG_MS,
        view_count=None,
    )

    chosen = choose(known, hidden)

    assert chosen is not None


@pytest.mark.parametrize("views", [0, 1, 912, 10**9])
def test_any_view_count_still_produces_a_match(views):
    chosen = choose(upload(f"{ARTIST} - Topic", views))

    assert chosen is not None


def test_identity_validation_runs_before_ranking():
    """A sanity check on the ordering of the two stages."""
    live = score_candidate(
        upload(f"{ARTIST}VEVO", 10**9, title=f"{ARTIST} - {TITLE} (Live)"),
        TITLE,
        ARTIST,
        SONG_MS,
    )

    assert not live.accepted
