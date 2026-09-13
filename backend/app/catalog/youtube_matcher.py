"""Choosing which YouTube video represents a song.

The hard part of using YouTube as a popularity signal is not fetching view
counts, it is making sure the count belongs to the right video. A search for any
well known song returns the official video alongside a lyric video, a live
performance, a sped up edit, a karaoke track, several reuploads, and usually a
reaction video, and those have wildly different view counts. Taking the first
result would produce a popularity number that is confidently wrong.

So this module is built to refuse. It scores candidates, requires a clear
winner, and sends anything uncertain to the unresolved queue rather than
guessing.

Signals used, in rough order of weight:

  - The channel looks like the artist's own, or a topic channel
  - The title contains the song title and the artist
  - The duration is close to the recording we already hold
  - The title says "official video" or "official audio"

Disqualifiers are treated as hard rejections rather than penalties, because a
live version of a song is a genuinely different recording with a genuinely
different audience, and averaging it in would corrupt the signal.
"""

import re
from dataclasses import dataclass
from enum import Enum

from app.providers.youtube.youtube_provider import YouTubeVideo
from app.services.guess_matcher import normalize_artist, normalize_title

# How far a video may differ from the known recording length. YouTube uploads
# carry intros, outros, and label idents, so this is looser than the catalog
# resolver's tolerance, but still tight enough to exclude extended mixes.
DURATION_TOLERANCE_MS = 12_000

# Wording that means this is not the canonical upload of the recording. These
# are rejections, not penalties: a live or remixed version is a different
# recording with a different audience.
# Grouped by category rather than as a flat list, because the exemption is per
# category. If the catalog song is itself a live recording, every live related
# phrase should be allowed, not just the exact one that happens to appear in
# both titles.
DISQUALIFYING: dict[str, tuple[str, ...]] = {
    "live": ("live at", "live from", "live in", "live on", "(live", "[live", "- live", "concert"),
    "remix": ("remix", "bootleg", "mashup", "edit by", "flip)"),
    "altered": ("sped up", "speed up", "spedup", "slowed", "reverb", "nightcore", "8d audio"),
    "cover": ("cover by", "cover)", "acoustic cover", "piano version", "ai cover", "ai version"),
    "karaoke": ("karaoke", "instrumental", "backing track"),
    "commentary": ("reaction", "reacts to", "review", "explained", "breakdown"),
    "tutorial": ("tutorial", "how to play", "lesson"),
    "looped": ("1 hour", "one hour", "10 hours", "loop", "extended"),
}

# Wording that marks the canonical upload.
OFFICIAL_MARKERS = ("official video", "official music video", "official audio", "official hd")
LYRIC_MARKERS = ("lyric video", "lyrics", "with lyrics")

# Channels YouTube generates for rights holders. Reliable and very common.
TOPIC_CHANNEL = " - topic"
VEVO = "vevo"

_BRACKETS = re.compile(r"[\(\[].*?[\)\]]")


class VideoMatchConfidence(str, Enum):
    """How sure we are that a video represents the song."""

    VERIFIED_OFFICIAL = "verified_official"
    HIGH_CONFIDENCE = "high_confidence"
    MANUAL = "manual"
    UNRESOLVED = "unresolved"

    @property
    def is_acceptable(self) -> bool:
        return self is not VideoMatchConfidence.UNRESOLVED


@dataclass(frozen=True)
class VideoMatch:
    """A scored candidate."""

    video: YouTubeVideo
    confidence: VideoMatchConfidence
    method: str
    score: int
    reason: str

    @property
    def accepted(self) -> bool:
        return self.confidence.is_acceptable


def _strip_brackets(text: str) -> str:
    return _BRACKETS.sub(" ", text)


def is_disqualified(video_title: str, song_title: str) -> str | None:
    """Return the disqualifying phrase, or None.

    Exemptions work per category. If the catalog song is itself a live
    recording, "Live at Wembley" must be allowed even though the song is only
    titled "- Live", so the whole live category is waived rather than the one
    phrase that happens to appear in both strings.
    """
    lowered = video_title.lower()
    song_lowered = song_title.lower()

    for phrases in DISQUALIFYING.values():
        if any(phrase in song_lowered for phrase in phrases):
            # The song is itself this kind of recording. Expected wording.
            continue
        for phrase in phrases:
            if phrase in lowered:
                return phrase
    return None


def channel_matches_artist(channel_title: str, artist: str) -> bool:
    """Whether a channel plausibly belongs to the artist.

    Covers the three shapes that actually occur: an artist's own channel, the
    auto-generated "Artist - Topic" channel, and a Vevo channel.
    """
    channel = normalize_artist(channel_title.replace(TOPIC_CHANNEL, ""))
    wanted = normalize_artist(artist)
    if not channel or not wanted:
        return False

    if channel == wanted:
        return True
    # "TheWeekndVEVO" against "The Weeknd".
    compact_channel = channel.replace(" ", "")
    compact_wanted = wanted.replace(" ", "")
    if compact_channel in (compact_wanted, compact_wanted + VEVO):
        return True
    return compact_wanted in compact_channel and len(compact_wanted) >= 4


def score_candidate(
    video: YouTubeVideo, song_title: str, song_artist: str, song_duration_ms: int | None
) -> VideoMatch:
    """Score one candidate video against the song."""
    disqualifier = is_disqualified(video.title, song_title)
    if disqualifier:
        return VideoMatch(
            video,
            VideoMatchConfidence.UNRESOLVED,
            "disqualified",
            0,
            f"video title contains '{disqualifier}'",
        )

    normalized_video_title = normalize_title(_strip_brackets(video.title))
    normalized_song_title = normalize_title(song_title)

    title_present = bool(normalized_song_title) and normalized_song_title in normalized_video_title
    if not title_present:
        return VideoMatch(
            video,
            VideoMatchConfidence.UNRESOLVED,
            "title_mismatch",
            0,
            f"song title not found in '{video.title[:60]}'",
        )

    is_topic = video.channel_title.lower().endswith(TOPIC_CHANNEL)
    official_channel = channel_matches_artist(video.channel_title, song_artist)
    lowered_title = video.title.lower()
    has_official_marker = any(marker in lowered_title for marker in OFFICIAL_MARKERS)
    has_lyric_marker = any(marker in lowered_title for marker in LYRIC_MARKERS)

    # The artist named in the video title, for uploads on channels that are not
    # the artist's own.
    artist_in_title = normalize_artist(song_artist).split(" ")[0] in lowered_title.replace(
        "-", " "
    )

    duration_known = song_duration_ms is not None and video.duration_ms is not None
    duration_close = (
        duration_known and abs(video.duration_ms - song_duration_ms) <= DURATION_TOLERANCE_MS
    )

    score = 0
    reasons = []

    if official_channel:
        score += 50
        reasons.append("artist channel")
    if is_topic:
        score += 30
        reasons.append("topic channel")
    if has_official_marker:
        score += 20
        reasons.append("official marker")
    if has_lyric_marker:
        # Legitimate but usually not the canonical upload, so it should lose to
        # the official video when both are present.
        score += 5
        reasons.append("lyric video")
    if duration_close:
        score += 25
        reasons.append("duration matches")
    elif duration_known:
        # Known and wrong is evidence against, unlike simply unknown.
        score -= 25
        reasons.append("duration differs")
    if artist_in_title:
        score += 10
        reasons.append("artist in title")

    # A topic channel is auto-generated by YouTube for a rights holder, so it is
    # about as reliable as an artist's own channel.
    if (official_channel or is_topic) and (duration_close or not duration_known):
        confidence = VideoMatchConfidence.VERIFIED_OFFICIAL
        method = "official_channel"
    elif score >= 45 and (duration_close or not duration_known):
        confidence = VideoMatchConfidence.HIGH_CONFIDENCE
        method = "metadata"
    else:
        confidence = VideoMatchConfidence.UNRESOLVED
        method = "weak"

    return VideoMatch(video, confidence, method, score, ", ".join(reasons) or "no signals")


def pick_best_video(
    candidates: list[YouTubeVideo],
    song_title: str,
    song_artist: str,
    song_duration_ms: int | None = None,
) -> VideoMatch | None:
    """Choose the video for a song, or return None to send it for review.

    Returns None when nothing scores well enough, and also when the top two
    acceptable candidates are close together. A near tie usually means the
    official video and a reupload are both plausible, and their view counts can
    differ by an order of magnitude, so guessing is worse than deferring.
    """
    if not candidates:
        return None

    scored = [
        score_candidate(video, song_title, song_artist, song_duration_ms)
        for video in candidates
    ]
    accepted = [match for match in scored if match.accepted]

    if not accepted:
        return None

    accepted.sort(key=lambda match: -match.score)
    best = accepted[0]

    if len(accepted) > 1:
        runner_up = accepted[1]
        # A clear winner needs real separation. Ten points is roughly the gap
        # between "official channel" and "some channel that named the artist".
        if best.score - runner_up.score < 10:
            return None

    return best


def best_rejection_reason(
    candidates: list[YouTubeVideo], song_title: str, song_artist: str, duration_ms: int | None
) -> str:
    """Explain why nothing was accepted, for the unresolved queue."""
    if not candidates:
        return "no YouTube results"

    scored = [
        score_candidate(video, song_title, song_artist, duration_ms) for video in candidates
    ]
    accepted = [match for match in scored if match.accepted]

    if len(accepted) > 1:
        return (
            f"ambiguous, top candidates scored {accepted[0].score} and {accepted[1].score}"
        )

    best = max(scored, key=lambda match: match.score)
    return f"no confident match, best was '{best.video.title[:50]}' ({best.reason})"
