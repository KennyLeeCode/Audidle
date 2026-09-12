"""Generate placeholder audio for the mock catalog.

The mock provider needs real, decodable audio files so the whole game can be
played and the clip timing verified before any external audio service exists.
Rather than ship copyrighted music, this script synthesizes one short, distinct
tonal loop per mock song using only the standard library.

Each track gets its own root note, chord shape, and tempo, so they are actually
distinguishable from one another. That matters: the point of the mock data is
to let you verify that a 0.01s clip of song A is not a 0.01s clip of song B.

Run from the backend directory:

    py scripts/generate_mock_audio.py
"""

from __future__ import annotations

import json
import math
import struct
import sys
import wave
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_ROOT / "app" / "data"
AUDIO_DIR = DATA_DIR / "audio"

SAMPLE_RATE = 22_050
DURATION_SECONDS = 20.0
AMPLITUDE = 0.28

# Chord shapes as semitone offsets from the root.
CHORD_SHAPES = [
    (0, 4, 7),       # major
    (0, 3, 7),       # minor
    (0, 5, 7),       # sus4
    (0, 4, 7, 11),   # major seventh
    (0, 3, 7, 10),   # minor seventh
]


def note_frequency(semitones_from_a4: int) -> float:
    """Equal temperament frequency for a semitone offset from A4 (440 Hz)."""
    return 440.0 * (2.0 ** (semitones_from_a4 / 12.0))


def render_track(index: int) -> bytes:
    """Render one distinct 16 bit mono PCM track as raw frames."""
    root = note_frequency(-24 + (index * 5) % 24)
    chord = CHORD_SHAPES[index % len(CHORD_SHAPES)]
    # Beats per second, varied per track so tempo is another distinguishing cue.
    pulse_hz = 1.6 + (index % 5) * 0.45
    total_samples = int(SAMPLE_RATE * DURATION_SECONDS)
    frames = bytearray()

    for n in range(total_samples):
        t = n / SAMPLE_RATE

        # Percussive amplitude envelope so the track has an obvious onset at
        # t=0. A track that fades in from silence would make the 0.01s stage
        # meaningless, which is exactly the content problem real songs have.
        beat_phase = (t * pulse_hz) % 1.0
        envelope = math.exp(-4.0 * beat_phase)

        sample = 0.0
        for voice, semitone in enumerate(chord):
            freq = root * (2.0 ** (semitone / 12.0))
            # Slight detune per voice keeps it from sounding like a test tone.
            freq *= 1.0 + 0.0015 * voice
            sample += math.sin(2.0 * math.pi * freq * t) / len(chord)

        # Global fade in and out, only at the very edges of the file, to avoid
        # clicks at file boundaries.
        edge = min(t, DURATION_SECONDS - t)
        edge_gain = min(1.0, max(0.0, edge / 0.02))

        value = int(sample * envelope * edge_gain * AMPLITUDE * 32767)
        frames += struct.pack("<h", max(-32768, min(32767, value)))

    return bytes(frames)


def main() -> int:
    catalog_path = DATA_DIR / "mock_songs.json"
    if not catalog_path.exists():
        print(f"catalog not found at {catalog_path}", file=sys.stderr)
        return 1

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    for index, song in enumerate(catalog["songs"]):
        target = AUDIO_DIR / song["audio_file"]
        if target.exists():
            continue
        with wave.open(str(target), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(render_track(index))
        print(f"wrote {target.name}")

    print(f"{len(catalog['songs'])} tracks available in {AUDIO_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
