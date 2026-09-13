"""Tests for the match-report diagnostic.

No network. The report is built from candidate lists directly, which is also the
point of splitting the search out of it.

The property worth protecting: this tool must agree with the enrichment path. It
calls the same score_candidate and pick_best_video, so a test that the report's
selection equals pick_best_video's is really a test that nobody has quietly
reimplemented the ranking inside the diagnostic.
"""

import pytest

from app.catalog.match_report import build_match_report, render_match_report, upload_kind
from app.catalog.youtube_matcher import pick_best_video, score_candidate
from app.providers.youtube.youtube_provider import YouTubeVideo

ARTIST = "Nova Vale"
TITLE = "Paper Lantern"
SONG_MS = 240_000


def upload(channel, views, title=None, duration_ms=SONG_MS, video_id=None):
    return YouTubeVideo(
        video_id=video_id or f"{channel[:5]}{views}",
        title=title or f"{ARTIST} - {TITLE}",
        channel_id="c",
        channel_title=channel,
        duration_ms=duration_ms,
        view_count=views,
    )


def report_for(*videos):
    return build_match_report(TITLE, ARTIST, SONG_MS, list(videos))


# -- A selected valid candidate ---------------------------------------------


def test_a_single_valid_candidate_is_selected():
    report = report_for(upload(f"{ARTIST}VEVO", 5_000_000, video_id="official"))

    assert report.resolved
    assert report.selected.video_id == "official"
    assert report.selected.valid
    assert "only candidate" in report.selection_reason


def test_the_selected_candidate_carries_its_evidence():
    report = report_for(
        upload(
            f"{ARTIST}VEVO",
            5_000_000,
            title=f"{ARTIST} - {TITLE} (Official Video)",
            video_id="official",
        )
    )

    assert report.selected.confidence == "verified_official"
    assert report.selected.kind == "VEVO"
    assert report.selected.reason


# -- No valid candidates -----------------------------------------------------


def test_no_valid_candidates_is_reported_as_unresolved():
    report = report_for(
        upload("Reaction Central", 9_000_000, title=f"{TITLE} REACTION", video_id="r"),
        upload("Karaoke Co", 800_000, title=f"{TITLE} karaoke", video_id="k"),
    )

    assert not report.resolved
    assert report.selected is None
    assert "identity validation" in report.unresolved_reason
    assert report.valid_candidates == []


def test_an_empty_search_is_reported_clearly():
    report = report_for()

    assert not report.resolved
    assert "no candidates" in report.unresolved_reason


def test_rejected_candidates_still_carry_a_reason():
    report = report_for(upload("Someone Else", 100, title="A Completely Different Song"))

    assert not report.candidates[0].valid
    assert report.candidates[0].reason


# -- Multiple valid candidates ----------------------------------------------


def test_the_best_of_several_valid_candidates_is_explained():
    report = report_for(
        upload(
            f"{ARTIST}VEVO",
            1_000_000_000,
            title=f"{ARTIST} - {TITLE} (Official Video)",
            duration_ms=262_000,
            video_id="video",
        ),
        upload(
            f"{ARTIST}VEVO",
            800_000_000,
            title=f"{ARTIST} - {TITLE} (Official Audio)",
            video_id="audio",
        ),
    )

    assert report.resolved
    assert report.selected.video_id == "video"
    assert len(report.valid_candidates) == 2
    assert "largest audience" in report.selection_reason


def test_a_more_authoritative_upload_explains_itself():
    report = report_for(
        upload(ARTIST, 40_000_000, video_id="artist"),
        upload(f"{ARTIST} - Topic", 55_000_000, video_id="topic"),
    )

    assert report.selected.video_id == "artist"
    assert "more authoritative" in report.selection_reason


# -- Live and remix rejection ------------------------------------------------


def test_a_live_upload_is_rejected_however_popular():
    report = report_for(
        upload(
            f"{ARTIST}VEVO",
            900_000_000,
            title=f"{ARTIST} - {TITLE} (Live at Glastonbury)",
            video_id="live",
        ),
        upload(f"{ARTIST} - Topic", 3_000_000, video_id="studio"),
    )

    live = next(line for line in report.candidates if line.video_id == "live")
    assert not live.valid
    assert "live" in live.reason
    assert report.selected.video_id == "studio"


def test_a_remix_is_rejected_however_popular():
    report = report_for(
        upload(
            f"{ARTIST}VEVO",
            700_000_000,
            title=f"{ARTIST} - {TITLE} (Kygo Remix)",
            video_id="remix",
        ),
        upload(f"{ARTIST} - Topic", 2_000_000, video_id="studio"),
    )

    remix = next(line for line in report.candidates if line.video_id == "remix")
    assert not remix.valid
    assert report.selected.video_id == "studio"


# -- Topic against VEVO, the popularity representation case ------------------


def test_a_vevo_upload_beats_a_tiny_topic_upload():
    """The case the whole two stage design exists for."""
    report = report_for(
        upload(f"{ARTIST} - Topic", 1_400_000, video_id="topic"),
        upload(
            f"{ARTIST}VEVO",
            1_500_000_000,
            title=f"{ARTIST} - {TITLE} (Official Video)",
            video_id="vevo",
        ),
    )

    assert report.selected.video_id == "vevo"
    assert report.selected.kind == "VEVO"
    # Both were valid. The Topic upload was not rejected, just not chosen.
    assert len(report.valid_candidates) == 2
    topic = next(line for line in report.candidates if line.video_id == "topic")
    assert topic.valid


def test_a_lone_topic_upload_is_still_selected():
    report = report_for(upload(f"{ARTIST} - Topic", 900, video_id="topic"))

    assert report.resolved
    assert report.selected.kind == "Topic"


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        (f"{ARTIST} - Topic", "Topic"),
        (f"{ARTIST}VEVO", "VEVO"),
        (ARTIST, "official artist"),
    ],
)
def test_upload_kind_labels(channel, expected):
    match = score_candidate(upload(channel, 1_000), TITLE, ARTIST, SONG_MS)

    assert upload_kind(match) == expected


# -- It must agree with the real enrichment path ----------------------------


def test_the_report_selection_matches_the_enrichment_selection():
    """Guards against the ranking being reimplemented inside the diagnostic."""
    videos = [
        upload(f"{ARTIST} - Topic", 1_400_000, video_id="topic"),
        upload(f"{ARTIST}VEVO", 1_500_000_000, video_id="vevo"),
        upload("Fan Uploads", 50_000_000, video_id="fan"),
        upload(f"{ARTIST}VEVO", 10_000, title=f"{TITLE} (Live)", video_id="live"),
    ]

    report = build_match_report(TITLE, ARTIST, SONG_MS, videos)
    enrichment_choice = pick_best_video(videos, TITLE, ARTIST, SONG_MS)

    assert report.selected.video_id == enrichment_choice.video.video_id


# -- Rendering ---------------------------------------------------------------


def test_rendering_a_resolved_report_mentions_the_selection():
    text = render_match_report(report_for(upload(f"{ARTIST}VEVO", 1_000, video_id="v")))

    assert "SELECTED" in text
    assert "chosen because" in text


def test_rendering_an_unresolved_report_says_so():
    text = render_match_report(
        report_for(upload("Reactions", 5, title=f"{TITLE} REACTION"))
    )

    assert "UNRESOLVED" in text
    assert "SELECTED" not in text
