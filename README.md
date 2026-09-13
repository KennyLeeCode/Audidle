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
| React frontend | Done |
| Spotify catalog integration | Done, search and metadata |
| Curated catalog and difficulty tiers | Done, 89 songs |
| Audio for real songs | **Open. Spotify cannot supply it, see below** |

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

## The curated catalog

Difficulty needs stream counts, and Spotify does not provide them, so the
catalog is held locally in SQLite. Two scripts build it:

```bash
py scripts/ingest_catalog.py     # resolve seed songs against Spotify, write rows
py scripts/attach_audio.py --status
```

`ingest_catalog.py` takes `app/data/seed_catalog.json`, which is a hand written
list of songs with estimated stream counts, and resolves each one against
Spotify search to get a real track id, ISRC, artwork, album, and release year.
It prefers the original release over remasters and sped up edits, which search
often ranks higher. Currently 89 of 89 resolve.

**The stream figures are estimates, not measurements.** They exist to bucket
songs into tiers, and each carries a `confidence` field reflecting how sure the
order of magnitude is. To use real data, replace the seed file with figures from
a chart source, keeping the same shape, and rerun. Nothing else changes.

Tier coverage is uneven by design of the data, not of the code: the easy tier
has 38 songs and the impossible tier has 2, because reliable figures below
roughly 25M streams are the hardest to estimate. That tier should be the first
one replaced with real data.

### Audio is the open blocker

Ingest produces rows with identity and difficulty and **no audio**, because no
API can legally supply it. A row with no audio file is never offered for
selection, so the game simply reports that a tier has no playable songs rather
than serving a broken round.

```bash
py scripts/attach_audio.py --from-dir ~/music/audidle   # attach files you hold
py scripts/attach_audio.py --placeholder                # dev only, synthesized tones
```

`--placeholder` generates unrelated synthesized audio so the pipeline can be
exercised end to end. It makes the game runnable, not playable, and says so when
you run it.

## What Spotify actually provides

Measured against a real app's credentials rather than assumed. For an app
created after Spotify's November 2024 restrictions:

| Endpoint | Status |
|---|---|
| `search` | 200 |
| `tracks/{id}` | 200 |
| `artists/{id}` (basic) | 200, but `genres` and `popularity` are null |
| `audio-features`, `audio-analysis` | 403 |
| `related-artists`, `artist top-tracks` | 403 |
| `recommendations` | 404 |

And on a track object, `preview_url` and `popularity` are not null, **the keys
are absent from the response entirely**:

```
['album', 'artists', 'disc_number', 'duration_ms', 'explicit', 'external_ids',
 'external_urls', 'href', 'id', 'is_local', 'is_playable', 'name',
 'track_number', 'type', 'uri']
```

There is no setting that restores them. The consequences:

**No audio.** Spotify cannot supply playable audio for this game. The only
supported route is the Web Playback SDK, which requires a Premium account per
player, an OAuth login, and playback inside a DRM iframe that cannot be
controlled at 10 millisecond precision. The 0.01s stage is not achievable
through it. Audio therefore comes from `AudioProvider` and never from Spotify.

**No stream counts and no popularity score.** Difficulty needs its own dataset
keyed by Spotify track id. `PopularityProvider` is a separate port for exactly
this reason.

**No genres.** Neither track nor artist genres are available, so the planned
genre filter cannot be driven by Spotify either.

What Spotify is genuinely excellent at, and what it is used for here: search,
track identity, titles, artists, albums, cover art, release dates, explicit
flags, ISRCs, and track links.

### Duplicate releases, and how guesses are matched

Real Spotify catalogs carry the same song under many track ids: the album cut,
the single, a remaster, a deluxe reissue. Searching "bohemian rhapsody" returns
at least three. Comparing track ids alone would mark a player wrong for picking
the remaster of the song they correctly identified.

`app/services/guess_matcher.py` applies three tests in order of confidence:

1. **Track id.** Exact, no false positives.
2. **ISRC.** Identifies the recording rather than the release, so it accepts a
   reissue of the same master while still separating a live version or a
   re-recording. Every ingested row has one.
3. **Normalized title and artist.** Strips release wording ("- Remastered
   2011", "(Single Version)"), featured credits, and accents. Requires the
   artist to match too, so two different songs called "Home" stay distinct.

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
