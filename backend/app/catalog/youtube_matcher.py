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

# How much longer than the recording an official upload may run and still be
# accepted. Covers cinematic intros and outros without admitting extended mixes
# or the hour long loops that pollute search results.
OFFICIAL_INTRO_ALLOWANCE_MS = 180_000

# Wording that means this is not the canonical upload of the recording. These
# are rejections, not penalties: a live or remixed version is a different
# recording with a different audience.
# Grouped by category rather than as a flat list, because the exemption is per
# category. If the catalog song is itself a live recording, every live related
# phrase should be allowed, not just the exact one that happens to appear in
# both titles.
DISQUALIFYING: dict[str, tuple[str, ...]] = {
    # Bare "live" is included, matched on a word boundary. It catches television
    # performances such as "Lucid Dreams (Jimmy Kimmel Live!/2018)", which an
    # earlier list of "live at / live from" phrases let through, making a TV
    # appearance the popularity signal for the song.
    "live": (
        "live",
        "concert",
        "unplugged",
        "session",
        "tiny desk",
        "performance",
    ),
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

# Wording YouTube uploads add that says nothing about which recording this is.
# Removed from the video title before comparison so the song title can be found
# inside it.
UPLOAD_NOISE = (
    "official music video", "official video", "official audio", "official hd video",
    "official lyric video", "official visualizer", "official trailer",
    "lyric video", "lyrics video", "with lyrics", "lyrics", "visualizer",
    "audio only", "full song", "hd", "hq", "4k", "1080p", "m/v", "mv",
    "explicit", "clean version", "official", "music video", "video",
)

# Qualifiers that identify *which recording* this is. These must agree between
# the catalog title and the video title, because a demo, a live take, and an
# acoustic rendition are different recordings with different audiences.
#
# Matched on word boundaries, since "remix" contains "mix" and "remastered"
# contains "remaster", and a substring check would collapse them.
# Note what is absent: "remaster". A remaster is the same performance run
# through a different mastering chain, and on YouTube the remastered upload is
# usually *the* official video, as with Queen's "Bohemian Rhapsody (Official
# Video Remastered)" at 2.1 billion views. Treating it as a distinct version
# rejected the obviously correct match. Demos, live takes, acoustic renditions,
# and remixes are different performances and stay in the list.
VERSION_TOKENS: dict[str, tuple[str, ...]] = {
    "demo": ("demo",),
    "live": ("live", "concert", "unplugged", "session"),
    "acoustic": ("acoustic",),
    "remix": ("remix", "bootleg", "flip"),
    "radio_edit": ("radio edit",),
    "instrumental": ("instrumental",),
    "edit": ("edit",),
    "mix": ("mix",),
    "single_version": ("single version",),
    "extended": ("extended",),
    "sped_up": ("sped up", "spedup", "slowed"),
}

_VERSION_PATTERNS = {
    name: re.compile(r"\b(" + "|".join(re.escape(word) for word in words) + r")\b", re.I)
    for name, words in VERSION_TOKENS.items()
}


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
    # Individual signals the scoring found, as stable tokens. Downstream rules
    # read these rather than parsing `reason`, which is prose for humans.
    evidence: frozenset[str] = frozenset()

    @property
    def accepted(self) -> bool:
        return self.confidence.is_acceptable


def _strip_brackets(text: str) -> str:
    return _BRACKETS.sub(" ", text)


def _strip_version_words(title: str) -> str:
    """Remove version wording, once the versions have already been compared."""
    text = title
    for pattern in _VERSION_PATTERNS.values():
        text = pattern.sub(" ", text)
    return text


def extract_versions(title: str) -> frozenset[str]:
    """Which version qualifiers a title claims.

    Applied to both the catalog title and the video title so they can be
    compared like for like. This is the fix for songs such as "Jasmine - Demo",
    where stripping brackets from "Jai Paul - Jasmine (Demo)" threw away the one
    word that made the two titles the same recording.

    "remix" is checked before "mix" and "remastered" before "remaster" by using
    word boundaries, so the longer token does not get read as the shorter one.
    """
    found = set()
    for name, pattern in _VERSION_PATTERNS.items():
        if pattern.search(title):
            found.add(name)

    # Longer qualifiers absorb the shorter ones they contain, so a radio edit
    # is not separately reported as an edit and a remix is not also a mix.
    if "remix" in found:
        found.discard("mix")
    if "radio_edit" in found:
        found.discard("edit")
    if "sped_up" in found:
        found.discard("edit")
    return frozenset(found)


def strip_upload_noise(title: str) -> str:
    """Remove YouTube's decoration, keeping version qualifiers intact.

    Deliberately does not strip brackets wholesale. "(Official Video)" is noise
    and "(Demo)" is identity, and they look identical to a bracket stripper.
    """
    text = title.lower()
    for phrase in UPLOAD_NOISE:
        text = re.sub(r"[\(\[]?\s*\b" + re.escape(phrase) + r"\b\s*[\)\]]?", " ", text)
    # Trailing separators left behind by the removals.
    return re.sub(r"[\|\-–—:]+\s*$", " ", text).strip()


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
    """Whether a channel actually belongs to the artist.

    Requires an exact match once the known official suffixes are stripped. An
    earlier version accepted any channel *containing* the artist name, which is
    how "mitski lyrics" was accepted as Mitski's own channel and a fan lyric
    video with 912 views became the popularity signal for Two Slow Dancers.

    Fan and aggregator channels almost always embed the artist name, so
    containment is not evidence of ownership. The shapes that are real:

        Mitski                 the artist's own channel
        TheWeekndVEVO          a Vevo channel
        Clairo - Topic         YouTube's auto-generated rights holder channel
        Ed Sheeran Official    an explicitly labelled official channel

    A leading "the" is ignored on both sides, so "ChainsmokersVEVO" matches
    "The Chainsmokers".
    """
    wanted = normalize_artist(artist)
    # Lowercased before stripping, because the channel is titled "Clairo - Topic"
    # and a case sensitive strip left "clairo topic", which no longer matched
    # once the loose containment rule was removed.
    lowered = channel_title.lower()
    if lowered.endswith(TOPIC_CHANNEL):
        lowered = lowered[: -len(TOPIC_CHANNEL)]
    channel = normalize_artist(lowered)
    if not channel or not wanted:
        return False

    compact_channel = channel.replace(" ", "")
    compact_wanted = wanted.replace(" ", "")

    # Suffixes that mark an official channel rather than a different one.
    # Deliberately short: "media", "lyrics", "hits", and "music" are omitted
    # because fan channels use them far more often than artists do.
    # "musicvevo" is checked before "vevo" so the longer suffix wins. A bare
    # "music" suffix is never stripped, since fan channels use it constantly,
    # but combined with Vevo it is unambiguously the artist's own channel.
    for suffix in ("musicvevo", "vevomusic", VEVO, "official", "officialchannel"):
        if compact_channel.endswith(suffix):
            compact_channel = compact_channel[: -len(suffix)]
            break

    for text in (compact_channel, compact_wanted):
        if not text:
            return False

    stripped_channel = compact_channel.removeprefix("the")
    stripped_wanted = compact_wanted.removeprefix("the")

    return stripped_channel == stripped_wanted


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

    # Version qualifiers are compared before the titles, because they decide
    # whether these are even the same recording.
    song_versions = extract_versions(song_title)
    video_versions = extract_versions(video.title)

    if song_versions != video_versions:
        return VideoMatch(
            video,
            VideoMatchConfidence.UNRESOLVED,
            "version_mismatch",
            0,
            f"version differs: song {sorted(song_versions) or 'studio'} against "
            f"video {sorted(video_versions) or 'studio'}",
        )

    # Base titles, with the version wording removed from both sides so it is not
    # counted twice, and upload decoration removed from the video only.
    normalized_video_title = normalize_title(
        _strip_version_words(strip_upload_noise(video.title))
    )
    normalized_song_title = normalize_title(_strip_version_words(song_title))

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
    # An official music video runs longer than the recording, because of intros,
    # spoken openings, and label idents. Running *shorter* is different: that is
    # usually a clip or a trailer, and is not treated as plausible.
    runs_longer_by = (video.duration_ms - song_duration_ms) if duration_known else 0
    plausibly_longer = duration_known and 0 < runs_longer_by <= OFFICIAL_INTRO_ALLOWANCE_MS

    score = 0
    reasons = []
    evidence: set[str] = set()

    if official_channel:
        score += 50
        reasons.append("artist channel")
        evidence.add("official_channel")
    if is_topic:
        score += 30
        reasons.append("topic channel")
        evidence.add("topic_channel")
    if has_official_marker:
        score += 20
        reasons.append("official marker")
        evidence.add("official_marker")
    if has_lyric_marker:
        # Legitimate but usually not the canonical upload, so it should lose to
        # the official video when both are present.
        score += 5
        reasons.append("lyric video")
    if duration_close:
        score += 25
        reasons.append("duration matches")
        evidence.add("duration_close")
    elif duration_known:
        # Known and wrong is evidence against, unlike simply unknown. But an
        # official music video legitimately runs longer than the recording,
        # because of intros, spoken openings, and label idents, so the penalty
        # is softened when the upload is clearly the artist's own. Without this
        # the matcher prefers the shorter "Official Audio" upload over the more
        # watched official video, which undercounts the popularity signal.
        if official_channel and plausibly_longer:
            score -= 5
            reasons.append("longer than the recording, official upload")
        else:
            score -= 25
            reasons.append("duration differs")
    if artist_in_title:
        score += 10
        reasons.append("artist in title")
        evidence.add("artist_in_title")

    # An official upload is acceptable when its length is either close to the
    # recording or plausibly longer, which is what a music video with an intro
    # looks like. Without the second case the matcher rejects the official video
    # and settles for the shorter official audio upload, which usually has far
    # fewer views and therefore understates the song.
    duration_acceptable = duration_close or not duration_known or plausibly_longer

    # A topic channel is auto-generated by YouTube for a rights holder, so it is
    # about as reliable as an artist's own channel.
    if (official_channel or is_topic) and duration_acceptable:
        confidence = VideoMatchConfidence.VERIFIED_OFFICIAL
        method = "official_channel"
    elif score >= 45 and duration_acceptable:
        confidence = VideoMatchConfidence.HIGH_CONFIDENCE
        method = "metadata"
    else:
        confidence = VideoMatchConfidence.UNRESOLVED
        method = "weak"

    if not song_versions and not video_versions:
        # Neither side claims an alternate version, so this is the original
        # studio recording rather than a demo, live take, or remix.
        evidence.add("studio_original")

    return VideoMatch(
        video,
        confidence,
        method,
        score,
        ", ".join(reasons) or "no signals",
        evidence=frozenset(evidence),
    )


# How much more watched a lower-authority upload must be before it is preferred
# over a higher-authority one. An artist's own upload wins by default, but not
# when an equally valid official upload has a thousand times the audience.
VIEW_DOMINANCE_FACTOR = 20

# The relaxed bar, available only to candidates carrying strong canonical
# evidence. It exists because the full 20x bar is the wrong shape for a common
# real case: an artist's own channel hosts an official *audio* upload while the
# official *music video* lives on the channel that produced it. The video is the
# canonical artifact and carries most of the audience, but the gap between them
# is usually single digit multiples rather than an order of magnitude.
CANONICAL_DOMINANCE_FACTOR = 5

# What a candidate must show before the relaxed bar applies. Every one of these
# is already required for acceptance except the official marker, so this is
# really asking: does the upload explicitly present itself as the official
# release of exactly this recording.
CANONICAL_EVIDENCE = frozenset(
    {"official_marker", "artist_in_title", "duration_close", "studio_original"}
)


def has_canonical_evidence(match: VideoMatch) -> bool:
    """Whether a candidate is strong enough to use the relaxed dominance bar.

    Deliberately not a channel allowlist. It asks for evidence in the metadata
    we already have: the upload names the artist, states an official marker,
    runs to within the tight duration tolerance of our recording, and claims no
    alternate version on either side.

    The honest limitation: a reupload that faithfully copies the official title
    would satisfy this. Identity validation has already rejected wrong artists,
    wrong lengths, and alternate versions, and the 5x audience bar still has to
    be cleared, so the damage such a reupload could do is bounded. A stricter
    rule would need a maintained list of label and production channels, which is
    exactly the hardcoding this avoids.
    """
    return (
        CANONICAL_EVIDENCE <= match.evidence
        and CONFIDENCE_RANK[match.confidence] >= 1
    )


def authority_rank(match: VideoMatch) -> int:
    """How authoritative an already-validated candidate is.

    Only ever applied to candidates that have passed identity validation, so
    this ranks *how good a popularity measurement* an upload is, not whether it
    is the right recording. Those are separate questions and conflating them is
    what put a 1.4M view Topic upload in front of a 1.5B view official video.
    """
    channel = match.video.channel_title.lower()
    if channel.endswith(TOPIC_CHANNEL):
        return 1
    if match.method == "official_channel":
        return 2
    return 0


CONFIDENCE_RANK = {
    VideoMatchConfidence.VERIFIED_OFFICIAL: 2,
    VideoMatchConfidence.MANUAL: 2,
    VideoMatchConfidence.HIGH_CONFIDENCE: 1,
    VideoMatchConfidence.UNRESOLVED: 0,
}


def _ranking_key(match: VideoMatch) -> tuple:
    """Order validated candidates by how well they represent the song.

    The video id is the final term so the order is total and does not depend on
    which candidate the search happened to return first.
    """
    return (
        authority_rank(match),
        CONFIDENCE_RANK[match.confidence],
        match.video.view_count or 0,
        match.score,
        match.video.video_id,
    )


def _best_popularity_representation(valid: list[VideoMatch]) -> VideoMatch:
    """Pick the best popularity measurement among validated candidates.

    Identity is already settled by the time this runs. Every candidate here has
    passed the artist, title, duration, and version checks, so the only question
    left is which upload best represents how widely the song has been heard.

    Authority and confidence lead, because an artist's own upload is usually the
    canonical one. But neither may *exclude* a validated candidate from the
    comparison, which is the mistake this function was written to fix: a
    production company's upload of the official music video carried 843 times
    the audience of the artist's Topic duplicate and never reached the ranking
    at all, because it was merely high confidence rather than verified.

    So a dramatically larger audience overrides seniority, across confidence
    levels. Both candidates are already known to be the same recording, so
    preferring the bigger one cannot change *which song* was measured, only
    which measurement of it is used.
    """
    leader = max(valid, key=_ranking_key)

    most_watched = max(
        valid, key=lambda match: (match.video.view_count or 0, match.video.video_id)
    )
    if most_watched is leader:
        return leader

    leader_views = leader.video.view_count or 0
    challenger_views = most_watched.video.view_count or 0

    # A candidate carrying strong canonical evidence needs a smaller audience
    # advantage to overturn seniority, because the evidence itself is telling us
    # it is the canonical artifact. An anonymous reupload gets no such discount
    # and must clear the full bar.
    factor = (
        CANONICAL_DOMINANCE_FACTOR
        if has_canonical_evidence(most_watched)
        else VIEW_DOMINANCE_FACTOR
    )

    if challenger_views >= max(leader_views, 1) * factor:
        return most_watched

    return leader


def pick_best_video(
    candidates: list[YouTubeVideo],
    song_title: str,
    song_artist: str,
    song_duration_ms: int | None = None,
) -> VideoMatch | None:
    """Choose the video for a song, or return None to send it for review.

    Two different situations look like a tie and are treated differently.

    Several verified uploads on the artist's own channel is not ambiguity about
    *which song* this is. A big single usually has both an official video and an
    official audio upload, and both are legitimately the song. The most watched
    one is chosen, because the question being answered is how widely heard the
    song is, and the canonical video carries that audience.

    A tie that is not all verified, or spans unrelated channels, is genuine
    ambiguity. Those view counts can differ by an order of magnitude, so the
    song goes to review instead.
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

    # The ambiguity guard, and the only case that still refuses outright: no
    # candidate is authoritative, none dominates on audience, and their scores
    # are too close to separate. That is two anonymous reuploads of unknown
    # provenance, and picking one would be a guess.
    if _is_ambiguous(accepted):
        return None

    # Every remaining candidate has passed identity validation, so ranking runs
    # across all of them. Confidence informs the order but never excludes.
    return _best_popularity_representation(accepted)


def _is_ambiguous(accepted: list[VideoMatch]) -> bool:
    """Whether a validated set has no defensible winner."""
    if len(accepted) < 2:
        return False

    if any(authority_rank(match) >= 1 for match in accepted):
        # Something authoritative is present, so the ranking has a basis.
        return False

    by_views = sorted(accepted, key=lambda match: -(match.video.view_count or 0))
    top_views = by_views[0].video.view_count or 0
    runner_up_views = by_views[1].video.view_count or 0
    if top_views >= max(runner_up_views, 1) * VIEW_DOMINANCE_FACTOR:
        # One upload clearly carries the audience, which is a basis too.
        return False

    by_score = sorted(accepted, key=lambda match: -match.score)
    # Ten points is roughly the gap between "official channel" and "some
    # channel that named the artist".
    return by_score[0].score - by_score[1].score < 10


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
