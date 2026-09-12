# Audidle

A music guessing game. You hear progressively longer clips of the same song
until you identify it, starting at 0.01 seconds.

Built as a portfolio project. React + TypeScript + Vite on the front, FastAPI on
the back, with the game rules enforced server side.

## How a round works

1. You pick a difficulty. The server draws one random song for that tier and
   opens a round on it. The response contains no information about the song.
2. You hear the first 0.01 seconds and guess.
3. A wrong guess or a skip unlocks the next clip length of the **same song**.
   The song never changes mid round.
4. Stages go 0.01s, 0.1s, 0.5s, 2s, 8s, 15s. Every clip replays from the same
   start point, it does not resume where the last one stopped.
5. Reaching 15s is not a loss. You hear that clip and get one final guess. A
   wrong guess or a skip there ends the round.
6. A correct guess at any stage wins immediately.
7. Win or lose, the round enters the reveal state. The song is shown and the
   full track plays. Nothing changes until you press Next Song.

## Project status

| Area | State |
|---|---|
| Backend game engine | Done, 61 tests passing |
| Mock providers (catalog, popularity, audio) | Done |
| REST API | Done |
| React frontend | Not started |
| Spotify catalog integration | Not started |
| Curated stream count dataset | Not started |

## Running the backend

```bash
cd backend
py -m venv .venv
.venv/Scripts/activate          # source .venv/bin/activate on macOS and Linux
pip install -e ".[dev]"

# Generate the placeholder audio the mock provider serves
py scripts/generate_mock_audio.py

uvicorn app.main:app --reload
```

Interactive API docs at http://127.0.0.1:8000/docs

```bash
pytest          # run the rule tests
ruff check .    # lint
```

Copy `.env.example` to `.env` to change anything. The defaults run the whole
game on mock data with no credentials required.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness and which providers are wired up |
| GET | `/api/config` | Stage ladder and difficulty tiers, for the client |
| GET | `/api/search?q=` | Song autocomplete for guessing |
| POST | `/api/game/round` | Open a round |
| GET | `/api/game/{id}` | Re-read round state |
| POST | `/api/game/{id}/guess` | Submit a guess |
| POST | `/api/game/{id}/skip` | Unlock the next clip length |
| POST | `/api/game/{id}/abandon` | Reroll onto a different song |
| GET | `/api/game/{id}/result` | Reveal, only after the round has ended |
| GET | `/api/game/{id}/audio` | This round's audio, behind an opaque URL |

## Architecture

```
React ──HTTP──> FastAPI routers ──> services ──> providers ──> outside world
                (HTTP only)         (the rules)   (swappable)
```

The backend is authoritative. It owns the answer, stage progression, and the
win or loss decision. The client renders and plays audio, and is never told
which song is correct until the round is over.

### Not leaking the answer

Three separate leaks are closed:

- `POST /api/game/round` returns no title, artist, album, artwork, or link.
- Audio is served from `/api/game/{roundId}/audio`, not from a filename. A URL
  like `/audio/blinding-lights.mp3` in the network tab would give the answer
  away just as effectively as putting it in the JSON.
- `GET /api/game/{id}/result` returns 409 for a round that is still playing.

Round ids are cryptographically random, so they cannot be enumerated.

### Provider abstraction

Three ports, because the outside world does not divide cleanly into one:

- **`SongCatalogProvider`** answers "what songs exist". Spotify is good at this.
- **`PopularityProvider`** answers "how well known is this". **Spotify's Web API
  does not expose stream counts**, only a 0-100 popularity score, so this is a
  separate port backed by a curated dataset.
- **`AudioProvider`** answers "how does the browser hear this". It returns a
  descriptor, not bytes, carrying `supports_precise_clips` so the frontend knows
  whether exact sub second clipping is possible.

Every provider is constructed in `app/dependencies.py` and nowhere else.
Switching implementations is an env var plus one factory.

### Central configuration

`app/config/stages.py` holds `CLIP_STAGES` and `app/config/difficulties.py`
holds the tier thresholds. Both are served to the client via `/api/config`, so
retuning the game is a one file backend change with no frontend deploy. Stage
count is always derived from the list, never hardcoded.

## Notes on Spotify

Three real limitations shaped the design, and none of them are worked around by
pretending:

**Stream counts are not available.** The Web API exposes `popularity`, a
relative 0-100 score with an undocumented formula. You cannot derive "1 billion
streams" from it. Difficulty tiers therefore declare both a stream range and a
fallback popularity band, and the active `PopularityProvider` reads whichever it
can honour.

**Preview URLs are unreliable.** Spotify restricted 30 second `preview_url`
access for newly created apps, and the field is frequently `null`. The game is
not built on it.

**Full track playback needs the Web Playback SDK**, which requires a Premium
account per player, an OAuth login, and playback inside Spotify's DRM player.
That player cannot be controlled at 10 millisecond precision, so the 0.01s stage
is not achievable through it. `supports_precise_clips` exists so the UI can say
so honestly rather than claiming a timing it is not delivering.

## The 0.01 second stage

10 milliseconds is real, but only on a decodable source. `<audio>` plus a
`setTimeout` cannot do it: timer clamping and playback startup latency put the
actual result somewhere between 0 and 300ms, and non deterministically so.

Web Audio can. `AudioBufferSourceNode.start(when, offset, duration)` schedules
on the audio hardware clock at sample resolution, which at 44.1kHz makes 0.01s
exactly 441 samples. The requirements are a fully decoded buffer and a user
gesture to unlock the context, both of which happen at round load.

Two details that matter at that length:

- **Clicks.** Cutting audio mid waveform produces a pop louder than the music.
  A short volume ramp fixes it, but a fixed 2ms fade would be a fifth of a 0.01s
  clip. `clip_fade_ms` scales the ramp with the clip instead.
- **Silence.** Many tracks open on a fade in, where 10ms is literally nothing.
  That is a content problem, solved by a per song start offset, which is the
  same mechanism the future Main Hook mode needs.

## Mock data

`app/data/mock_songs.json` is a fictional catalog. Titles and artists are
invented so it is obviously placeholder data. `scripts/generate_mock_audio.py`
synthesizes one distinct tonal loop per track using only the standard library,
so the whole game is playable and the clip timing is verifiable with no external
service and no copyrighted audio. The generated `.wav` files are gitignored.

## License

Not yet licensed.
