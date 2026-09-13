"""Tests for YouTube matching, quota handling, and popularity normalization.

No live calls. The interesting part is the matcher, and the interesting cases
are the ones it must refuse. A search for any well known song returns the
official video next to a lyric video, a live cut, a sped up edit, a karaoke
track, and a reaction video, and those have wildly different view counts.
Attaching the wrong one produces a popularity number that is confidently wrong,
which is worse than having none.
"""

import httpx
import pytest

from app.catalog.youtube_matcher import (
    VideoMatchConfidence,
    channel_matches_artist,
    is_disqualified,
    pick_best_video,
    score_candidate,
)
from app.config.popularity import (
    difficulty_from_score,
    log_normalize,
    score_from_youtube_views,
    views_for_score,
)
from app.core.errors import ProviderConfigurationError
from app.models.enums import Difficulty
from app.providers.youtube.youtube_provider import (
    COST_SEARCH,
    COST_VIDEOS,
    QuotaExhaustedError,
    YouTubePopularityProvider,
    parse_iso8601_duration,
)

SONG_TITLE = "Blinding Lights"
SONG_ARTIST = "The Weeknd"
SONG_MS = 200_040


def video(title, channel="TheWeekndVEVO", duration_ms=200_000, views=100, vid="v1"):
    from app.providers.youtube.youtube_provider import YouTubeVideo

    return YouTubeVideo(
        video_id=vid,
        title=title,
        channel_id="c1",
        channel_title=channel,
        duration_ms=duration_ms,
        view_count=views,
    )


# -- Disqualifiers -----------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Blinding Lights (Live at the BRITs)",
        "Blinding Lights - Live from Wembley",
        "Blinding Lights (Sped Up)",
        "Blinding Lights slowed + reverb",
        "Blinding Lights KARAOKE version",
        "Blinding Lights - Instrumental",
        "Blinding Lights REACTION!!",
        "Blinding Lights (Cover by Someone)",
        "Blinding Lights remix",
        "Blinding Lights 1 hour loop",
        "Blinding Lights AI cover",
    ],
)
def test_non_canonical_uploads_are_disqualified(title):
    """These are different recordings with different audiences."""
    assert is_disqualified(title, SONG_TITLE) is not None


def test_a_live_song_is_not_disqualified_by_the_word_live():
    """If the catalog song is itself a live recording, that wording is expected."""
    assert is_disqualified("Hyper Ballad - Live at Shepherds Bush", "Hyper Ballad - Live") is None


def test_the_official_video_is_not_disqualified():
    assert is_disqualified("The Weeknd - Blinding Lights (Official Video)", SONG_TITLE) is None


# -- Channel identity --------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        ("The Weeknd", True),
        ("TheWeekndVEVO", True),
        ("The Weeknd - Topic", True),
        ("TheWeekndOfficial", True),
        ("Beyoncé", False),
        ("Random Music Uploads", False),
        ("", False),
    ],
)
def test_channel_identity(channel, expected):
    assert channel_matches_artist(channel, SONG_ARTIST) is expected


# -- Scoring -----------------------------------------------------------------


def test_the_official_video_is_verified():
    match = score_candidate(
        video("The Weeknd - Blinding Lights (Official Video)"),
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert match.confidence is VideoMatchConfidence.VERIFIED_OFFICIAL
    assert match.accepted


def test_a_topic_channel_is_verified():
    """YouTube generates these for rights holders, so they are reliable."""
    match = score_candidate(
        video("Blinding Lights", channel="The Weeknd - Topic"), SONG_TITLE, SONG_ARTIST, SONG_MS
    )

    assert match.confidence is VideoMatchConfidence.VERIFIED_OFFICIAL


def test_a_random_channel_with_a_wrong_duration_is_unresolved():
    match = score_candidate(
        video("Blinding Lights", channel="Music Uploads 24/7", duration_ms=600_000),
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert match.confidence is VideoMatchConfidence.UNRESOLVED


def test_a_title_that_does_not_contain_the_song_is_rejected():
    match = score_candidate(video("Save Your Tears"), SONG_TITLE, SONG_ARTIST, SONG_MS)

    assert match.confidence is VideoMatchConfidence.UNRESOLVED
    assert "not found" in match.reason


def test_a_known_wrong_duration_counts_against_a_candidate():
    close = score_candidate(
        video("Blinding Lights", channel="Some Channel"), SONG_TITLE, SONG_ARTIST, SONG_MS
    )
    far = score_candidate(
        video("Blinding Lights", channel="Some Channel", duration_ms=900_000),
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert close.score > far.score


def test_an_unknown_duration_does_not_count_against_a_candidate():
    """Plenty of uploads report no duration, and that is not evidence."""
    match = score_candidate(
        video("The Weeknd - Blinding Lights (Official Video)", duration_ms=None),
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert match.accepted


# -- Choosing -----------------------------------------------------------------


def test_the_official_video_beats_a_lyric_video():
    chosen = pick_best_video(
        [
            video("Blinding Lights (Lyric Video)", channel="Lyrics Channel", vid="lyric"),
            video("The Weeknd - Blinding Lights (Official Video)", vid="official"),
        ],
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert chosen is not None
    assert chosen.video.video_id == "official"


def test_nothing_is_chosen_when_two_unofficial_candidates_are_close():
    """A reupload and a compilation channel can differ by an order of magnitude.

    Two official uploads on the artist's own channel are handled differently,
    see test_the_most_watched_official_upload_wins.
    """
    chosen = pick_best_video(
        [
            video("The Weeknd - Blinding Lights", channel="Music Archive", vid="a"),
            video("The Weeknd - Blinding Lights", channel="Best Hits 2020", vid="b"),
        ],
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert chosen is None


def test_nothing_is_chosen_when_every_candidate_is_junk():
    chosen = pick_best_video(
        [
            video("Blinding Lights REACTION", channel="Reactor", vid="r"),
            video("Blinding Lights karaoke", channel="Karaoke Co", vid="k"),
        ],
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert chosen is None


def test_no_candidates_returns_nothing():
    assert pick_best_video([], SONG_TITLE, SONG_ARTIST, SONG_MS) is None


# -- Quota --------------------------------------------------------------------


def test_an_api_key_is_required():
    with pytest.raises(ProviderConfigurationError):
        YouTubePopularityProvider(api_key="")


def test_quota_budget_leaves_a_reserve():
    provider = YouTubePopularityProvider("k", daily_quota=10_000, quota_reserve=200)

    assert provider.quota_remaining == 9_800
    assert provider.can_afford(COST_SEARCH)


def test_affordability_is_checked_before_spending():
    """A run must stop cleanly rather than failing part way through a song."""
    provider = YouTubePopularityProvider("k", daily_quota=150, quota_reserve=0)
    provider.quota_used = 100

    assert not provider.can_afford(COST_SEARCH)
    assert provider.can_afford(COST_VIDEOS)


@pytest.mark.anyio
async def test_exceeding_the_budget_raises_a_stopping_signal():
    provider = YouTubePopularityProvider("k", daily_quota=50, quota_reserve=0)

    with pytest.raises(QuotaExhaustedError):
        await provider.search_song_video(SONG_TITLE, SONG_ARTIST)


@pytest.mark.anyio
async def test_a_quota_error_from_youtube_stops_the_run():
    provider = YouTubePopularityProvider("k", daily_quota=10_000)
    provider._http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, text='{"error":{"message":"quotaExceeded"}}')
        )
    )

    with pytest.raises(QuotaExhaustedError):
        await provider.search_song_video(SONG_TITLE, SONG_ARTIST)


@pytest.mark.anyio
async def test_a_bad_api_key_is_reported_as_configuration():
    provider = YouTubePopularityProvider("k", daily_quota=10_000)
    provider._http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, text='{"error":{"message":"forbidden"}}')
        )
    )

    with pytest.raises(ProviderConfigurationError):
        await provider.search_song_video(SONG_TITLE, SONG_ARTIST)


@pytest.mark.anyio
async def test_repeat_searches_do_not_pay_twice():
    """A search costs 100 units, so caching is worth real quota."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": []})

    provider = YouTubePopularityProvider("k", daily_quota=10_000)
    provider._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await provider.search_song_video(SONG_TITLE, SONG_ARTIST)
    await provider.search_song_video(SONG_TITLE, SONG_ARTIST)

    assert len(calls) == 1
    assert provider.quota_used == COST_SEARCH


@pytest.mark.anyio
async def test_refreshing_many_videos_costs_one_unit_per_fifty():
    """The reason refreshing a whole catalog is nearly free."""
    def handler(request):
        return httpx.Response(200, json={"items": []})

    provider = YouTubePopularityProvider("k", daily_quota=10_000)
    provider._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await provider.refresh_video_stats([f"v{index}" for index in range(120)])

    # 120 ids means three calls, three units.
    assert provider.quota_used == 3


# -- Parsing ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PT3M20S", 200_000),
        ("PT1H2M3S", 3_723_000),
        ("PT45S", 45_000),
        ("PT4M", 240_000),
        (None, None),
        ("nonsense", None),
    ],
)
def test_iso8601_durations(value, expected):
    assert parse_iso8601_duration(value) == expected


# -- Normalization ------------------------------------------------------------


def test_normalization_is_logarithmic():
    """Each tenfold increase should move the score by a constant amount."""
    steps = [score_from_youtube_views(10**power) for power in range(5, 10)]
    gaps = [b - a for a, b in zip(steps, steps[1:], strict=False)]

    # Constant to within rounding, which is all the 2dp score allows.
    assert max(gaps) - min(gaps) < 0.05


def test_the_scale_is_bounded():
    assert score_from_youtube_views(0) == 0
    assert score_from_youtube_views(1) == 0
    assert score_from_youtube_views(10**12) == 100


def test_more_views_always_scores_higher():
    scores = [score_from_youtube_views(v) for v in (50_000, 1_000_000, 50_000_000, 2_000_000_000)]

    assert scores == sorted(scores)


def test_billion_view_songs_do_not_swamp_the_scale():
    """The point of the log curve.

    A billion views is a thousand times a million. On a linear scale the
    smaller song would score 0.1 out of 100 and every tier below Easy would be
    indistinguishable. Here it keeps a third of the scale.
    """
    modest = score_from_youtube_views(1_000_000)
    huge = score_from_youtube_views(1_000_000_000)

    assert modest > 30
    assert huge < 95
    # A 1000x difference in views is a 55 point difference in score, not 99.9.
    assert 50 < huge - modest < 60


def test_the_curve_inverts():
    for score in (20.0, 50.0, 80.0):
        assert abs(score_from_youtube_views(views_for_score(score)) - score) < 1.0


def test_log_normalize_handles_a_degenerate_range():
    assert log_normalize(500, floor=1_000, ceiling=10_000) == 0


def test_scores_bucket_into_tiers():
    assert difficulty_from_score(95.0) is Difficulty.EASY
    assert difficulty_from_score(10.0) is Difficulty.IMPOSSIBLE


# -- Official uploads that run longer than the recording ---------------------
#
# Found by a live smoke test rather than by reasoning. Searching "Blinding
# Lights" returns the official video at 1.06B views running 4:22, and the
# official audio at 879M views running 3:20. The catalog stores the 3:20
# recording, so a strict duration check rejected the official video and settled
# for the audio upload, understating the song by 180 million views.


def test_an_official_video_with_an_intro_is_still_accepted():
    """Music videos legitimately run longer than the recording."""
    match = score_candidate(
        video(
            "The Weeknd - Blinding Lights (Official Video)",
            duration_ms=262_000,  # 4:22 against a 3:20 recording
        ),
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert match.confidence is VideoMatchConfidence.VERIFIED_OFFICIAL


def test_an_upload_much_longer_than_the_recording_is_still_rejected():
    """The allowance covers intros, not hour long loops or extended mixes."""
    match = score_candidate(
        video("Blinding Lights", channel="Some Channel", duration_ms=1_800_000),
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert match.confidence is VideoMatchConfidence.UNRESOLVED


def test_an_upload_much_shorter_than_the_recording_is_rejected():
    """Shorter usually means a clip or a trailer, not the song."""
    match = score_candidate(
        video("The Weeknd - Blinding Lights (Official Video)", duration_ms=30_000),
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert match.confidence is not VideoMatchConfidence.VERIFIED_OFFICIAL


def test_the_most_watched_official_upload_wins():
    """Both are legitimately the song, so this is not ambiguity.

    The question being answered is how widely heard the song is, and the
    canonical video carries that audience.
    """
    chosen = pick_best_video(
        [
            video(
                "The Weeknd - Blinding Lights (Official Audio)",
                duration_ms=200_000,
                views=878_935_740,
                vid="audio",
            ),
            video(
                "The Weeknd - Blinding Lights (Official Video)",
                duration_ms=262_000,
                views=1_063_243_802,
                vid="video",
            ),
        ],
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert chosen is not None
    assert chosen.video.video_id == "video"


def test_unverified_ties_are_still_sent_to_review():
    """Ambiguity across unrelated channels is a different problem."""
    chosen = pick_best_video(
        [
            video("The Weeknd - Blinding Lights", channel="Uploads A", vid="a"),
            video("The Weeknd - Blinding Lights", channel="Uploads B", vid="b"),
        ],
        SONG_TITLE,
        SONG_ARTIST,
        SONG_MS,
    )

    assert chosen is None


# -- Rate limiting is not quota exhaustion -----------------------------------
#
# Found during the retry run: eight songs were lost to HTTP 429, which the
# provider treated as fatal. A 429 passes on its own after a short wait. A 403
# carrying "quota" does not.


@pytest.mark.anyio
async def test_a_429_is_retried_rather_than_failing():
    responses = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"items": []}),
    ]

    def handler(request):
        return responses.pop(0)

    provider = YouTubePopularityProvider("k", daily_quota=10_000)
    provider._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await provider.search_song_video(SONG_TITLE, SONG_ARTIST)

    assert result == []
    assert not responses


@pytest.mark.anyio
async def test_sustained_429s_raise_a_rate_limit_error_not_a_quota_error():
    """These must stay distinguishable, because they need different responses."""
    from app.core.errors import CatalogRateLimitedError

    provider = YouTubePopularityProvider("k", daily_quota=10_000)
    provider._http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "0"})
        )
    )

    with pytest.raises(CatalogRateLimitedError) as caught:
        await provider.search_song_video(SONG_TITLE, SONG_ARTIST)

    assert not isinstance(caught.value, QuotaExhaustedError)
