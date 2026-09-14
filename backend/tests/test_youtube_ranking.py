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

from app.catalog.youtube_matcher import (
    VideoMatchConfidence,
    has_canonical_evidence,
    pick_best_video,
    score_candidate,
)
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


# -- Confidence must rank, never exclude ------------------------------------
#
# Found on real data. A production company's upload of an official music video
# carried 843 times the audience of the artist's own Topic duplicate, and was
# never even compared against it: the ranking filtered to VERIFIED_OFFICIAL
# first, and the production company's channel only earned HIGH_CONFIDENCE.
#
# Identity validation is the hard gate. Confidence and authority order what
# survives it, and must not remove an otherwise valid candidate from the
# comparison entirely.


def production_upload(views, video_id="production", title=None):
    """A canonical upload on a channel that is not the artist's own.

    Scores as high confidence: the title carries an official marker and the
    artist name, and the duration matches, but the channel is a third party.
    """
    return upload(
        "Lyric Label Productions",
        views,
        title=title or f"{ARTIST} - {TITLE} (Official Music Video)",
        duration_ms=231_000,
        video_id=video_id,
    )


def test_a_dominant_high_confidence_upload_beats_a_verified_topic_upload():
    """Case 1. The exact shape that was silently losing."""
    topic = upload(f"{ARTIST} - Topic", 1_434_458, video_id="topic")
    canonical = production_upload(1_209_915_124)

    chosen = choose(topic, canonical)

    assert chosen is not None
    assert chosen.video.video_id == "production"
    # And the Topic upload was never rejected, only out-ranked.
    topic_match = score_candidate(topic, TITLE, ARTIST, SONG_MS)
    assert topic_match.accepted
    assert topic_match.confidence is VideoMatchConfidence.VERIFIED_OFFICIAL


def test_a_slightly_more_watched_high_confidence_upload_does_not_win():
    """Case 2. Dominance is for order-of-magnitude gaps, not narrow leads."""
    topic = upload(f"{ARTIST} - Topic", 40_000_000, video_id="topic")
    canonical = production_upload(55_000_000)

    chosen = choose(topic, canonical)

    assert chosen.video.video_id == "topic"


def test_a_verified_upload_wins_when_audiences_are_equal():
    topic = upload(f"{ARTIST} - Topic", 10_000_000, video_id="topic")
    canonical = production_upload(10_000_000)

    assert choose(topic, canonical).video.video_id == "topic"


@pytest.mark.parametrize(
    ("title", "label"),
    [
        (f"{ARTIST} - {TITLE} (Live at Wembley)", "live"),
        (f"{ARTIST} - {TITLE} (Kygo Remix)", "remix"),
        (f"{ARTIST} - {TITLE} (Acoustic)", "acoustic"),
        (f"{ARTIST} - {TITLE} REACTION", "reaction"),
    ],
)
def test_a_huge_invalid_candidate_never_reaches_the_ranking(title, label):
    """Case 3. Identity validation still runs first and is not negotiable."""
    invalid = production_upload(5_000_000_000, video_id="invalid", title=title)
    topic = upload(f"{ARTIST} - Topic", 50_000, video_id="topic")

    assert not score_candidate(invalid, TITLE, ARTIST, SONG_MS).accepted
    assert choose(invalid, topic).video.video_id == "topic"


def test_a_dominant_upload_by_the_wrong_artist_never_reaches_the_ranking():
    wrong = upload(
        "Some Other Act",
        4_000_000_000,
        title=f"Some Other Act - {TITLE}",
        video_id="wrong",
    )
    topic = upload(f"{ARTIST} - Topic", 20_000, video_id="topic")

    assert choose(wrong, topic).video.video_id == "topic"


def test_selection_is_order_independent_across_confidence_levels():
    """Case 4. Deterministic regardless of what the search returned first."""
    topic = upload(f"{ARTIST} - Topic", 1_400_000, video_id="topic")
    canonical = production_upload(1_200_000_000)
    vevo = upload(
        f"{ARTIST}VEVO",
        30_000_000,
        title=f"{ARTIST} - {TITLE} (Official Audio)",
        video_id="vevo",
    )

    orders = [
        (topic, canonical, vevo),
        (canonical, vevo, topic),
        (vevo, topic, canonical),
        (canonical, topic, vevo),
    ]
    picked = {choose(*order).video.video_id for order in orders}

    assert picked == {"production"}


def test_an_exact_view_tie_resolves_the_same_way_in_either_order():
    """Determinism is the property, whatever the outcome happens to be.

    Two anonymous uploads with identical audiences and identical scores are
    genuinely ambiguous, so both orderings refuse. What matters is that they
    refuse *consistently* rather than depending on search result order.
    """
    left = upload(
        "Music Archive A",
        5_000_000,
        title=f"{ARTIST} - {TITLE} (Official Video)",
        video_id="aaa",
    )
    right = upload(
        "Music Archive B",
        5_000_000,
        title=f"{ARTIST} - {TITLE} (Official Video)",
        video_id="zzz",
    )

    assert choose(left, right) == choose(right, left)


def test_an_authoritative_tie_resolves_deterministically():
    """When a winner does exist, ties break on a stable key, not arrival order."""
    topic = upload(f"{ARTIST} - Topic", 5_000_000, video_id="aaa")
    canonical = production_upload(5_000_000, video_id="zzz")

    assert (
        choose(topic, canonical).video.video_id
        == choose(canonical, topic).video.video_id
    )


def test_two_anonymous_reuploads_are_still_refused():
    """The ambiguity guard survives the change.

    Nothing authoritative, nothing dominant, scores too close to separate.
    """
    left = upload(
        "Music Archive", 5_000_000, title=f"{ARTIST} - {TITLE} (Official Video)", video_id="a"
    )
    right = upload(
        "Best Hits", 6_000_000, title=f"{ARTIST} - {TITLE} (Official Video)", video_id="b"
    )

    assert choose(left, right) is None


def test_one_dominant_anonymous_upload_is_not_ambiguous():
    """A clear audience leader is a basis for choosing, even without authority."""
    small = upload(
        "Music Archive", 100_000, title=f"{ARTIST} - {TITLE} (Official Video)", video_id="small"
    )
    large = upload(
        "Big Archive", 900_000_000, title=f"{ARTIST} - {TITLE} (Official Video)", video_id="large"
    )

    chosen = choose(small, large)

    assert chosen is not None
    assert chosen.video.video_id == "large"


# -- The constrained dominance bar ------------------------------------------
#
# The full 20x bar is the wrong shape for a common real case: an artist's own
# channel hosts an official *audio* upload while the official *music video*
# lives on the channel that produced it. The video carries most of the audience
# but the gap is usually single digit multiples.
#
# So a candidate showing strong canonical evidence gets a 5x bar instead. An
# anonymous reupload gets no discount. The privilege is earned from metadata we
# already have, never from a list of channel names.


def canonical_upload(views, video_id="canonical", channel="Indie Film Collective"):
    """An official music video hosted somewhere other than the artist channel.

    Names the artist, states an official marker, matches the recording length,
    and claims no alternate version. Lower authority, strong evidence.
    """
    return upload(
        channel,
        views,
        title=f"{ARTIST} - {TITLE} (Official Music Video)",
        video_id=video_id,
    )


def official_audio(views, video_id="audio"):
    """The artist's own channel hosting an official audio upload."""
    return upload(
        ARTIST,
        views,
        title=f"{ARTIST} - {TITLE} (Official Audio)",
        video_id=video_id,
    )


def bare_reupload(views, video_id="reupload"):
    """An accepted upload that lacks canonical evidence.

    It clears identity validation on an official marker and a matching
    duration, but never names the artist, so it does not present itself as the
    official release of this specific recording.
    """
    return upload(
        "Daily Music Uploads",
        views,
        title=f"{TITLE} (Official Video)",
        video_id=video_id,
    )


def test_canonical_evidence_is_recognised():
    match = score_candidate(canonical_upload(1_000), TITLE, ARTIST, SONG_MS)

    assert match.accepted
    assert has_canonical_evidence(match)
    assert match.confidence is VideoMatchConfidence.HIGH_CONFIDENCE


def test_a_bare_reupload_has_no_canonical_evidence():
    """Accepted as the right recording, but not as the official release of it."""
    match = score_candidate(bare_reupload(1_000), TITLE, ARTIST, SONG_MS)

    assert match.accepted
    assert not has_canonical_evidence(match)


def test_a_canonical_candidate_wins_at_seven_times_the_audience():
    """Case 1. Below the 20x bar, above the 5x one."""
    chosen = choose(official_audio(166_000_000), canonical_upload(1_209_000_000))

    assert chosen is not None
    assert chosen.video.video_id == "canonical"


def test_a_canonical_candidate_loses_at_twice_the_audience():
    """Case 2. The relaxed bar is 5x, not any advantage at all."""
    chosen = choose(official_audio(166_000_000), canonical_upload(332_000_000))

    assert chosen.video.video_id == "audio"


def test_the_relaxed_bar_is_exactly_five_times():
    below = choose(official_audio(100_000_000), canonical_upload(499_000_000))
    at_or_above = choose(official_audio(100_000_000), canonical_upload(500_000_000))

    assert below.video.video_id == "audio"
    assert at_or_above.video.video_id == "canonical"


def test_a_reupload_does_not_get_the_relaxed_bar():
    """Case 3. Seven times the audience, but no canonical evidence."""
    chosen = choose(official_audio(166_000_000), bare_reupload(1_209_000_000))

    assert chosen.video.video_id == "audio"


def test_a_reupload_still_wins_under_the_full_bar():
    """The normal 20x rule is unchanged for candidates without evidence."""
    chosen = choose(official_audio(10_000_000), bare_reupload(900_000_000))

    assert chosen.video.video_id == "reupload"


@pytest.mark.parametrize(
    "title_suffix",
    ["(Live at Wembley)", "(Kygo Remix)", "(Acoustic)", "(Sped Up)", "REACTION"],
)
def test_an_alternate_version_never_earns_canonical_evidence(title_suffix):
    """Case 4. Identity validation rejects these before evidence matters."""
    alternate = upload(
        "Indie Film Collective",
        9_000_000_000,
        title=f"{ARTIST} - {TITLE} {title_suffix} (Official Music Video)",
        video_id="alternate",
    )

    assert not score_candidate(alternate, TITLE, ARTIST, SONG_MS).accepted
    assert choose(alternate, official_audio(1_000_000)).video.video_id == "audio"


def test_a_topic_upload_still_wins_when_it_is_the_best_representation():
    """Case 5. Nothing about this change penalises Topic channels."""
    topic = upload(f"{ARTIST} - Topic", 40_000_000, video_id="topic")
    weak_canonical = canonical_upload(5_000_000, video_id="canonical")

    assert choose(topic, weak_canonical).video.video_id == "topic"


def test_a_lone_topic_upload_is_unaffected():
    chosen = choose(upload(f"{ARTIST} - Topic", 1_000, video_id="topic"))

    assert chosen.video.video_id == "topic"


def test_the_constrained_rule_is_order_independent():
    """Case 6."""
    audio = official_audio(166_000_000)
    canonical = canonical_upload(1_209_000_000)
    topic = upload(f"{ARTIST} - Topic", 1_400_000, video_id="topic")

    orders = [
        (audio, canonical, topic),
        (topic, audio, canonical),
        (canonical, topic, audio),
        (topic, canonical, audio),
    ]

    assert {choose(*order).video.video_id for order in orders} == {"canonical"}
