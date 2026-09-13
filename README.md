# Audidle

A music guessing game you play in the browser. You hear a tiny piece of a song
and try to name it, and the clip gets longer every time you get it wrong.

## How It Works

You start with 0.01 seconds of a song. That is not a typo, it really is ten
milliseconds.

Every wrong guess or skip unlocks a longer clip of the same song. The stages go
0.01s, 0.1s, 0.5s, 2s, 8s, and 15s. The song never changes in the middle of a
round, you just get more of it.

You get one last guess at the 15 second stage. Guess right at any point and you
win. Guess wrong or skip at 15 seconds and the round is over.

Either way the song is revealed at the end, and the full track plays while you
look at the title, artist, and album art. You stay on that screen until you
press Next Song.

## Difficulties

There are five: Easy, Medium, Hard, Expert, and Impossible.

Difficulty is based on how popular a song is, using estimated Spotify stream
counts. Easy is songs with over a billion streams, Impossible is under 25
million. The thresholds live in one config file so they are easy to change.

The stream numbers are estimates I put together by hand, not exact figures.
Spotify's API does not give out stream counts, so I keep that data myself.

## Built With

- React
- TypeScript
- Vite
- Python
- FastAPI
- SQLite with SQLAlchemy
- MusicBrainz API
- Spotify Web API

## Running Locally

```bash
git clone https://github.com/KennyLeeCode/Audidle.git
cd Audidle
```

Backend:

```bash
cd backend
py -m venv .venv
.venv/Scripts/activate        # source .venv/bin/activate on Mac or Linux
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in your Spotify client ID and secret from
the [Spotify developer dashboard](https://developer.spotify.com/dashboard), plus
an email address for `MUSICBRAINZ_CONTACT`. MusicBrainz asks for a contact
address in the request headers.

Build the song catalog:

```bash
py -m app.catalog migrate-curated
py -m app.catalog enrich-musicbrainz
py -m app.catalog stats
```

There are a few other catalog commands that are useful when something looks
wrong:

```bash
py -m app.catalog validate                       # what is missing and why
py -m app.catalog match-report --song "Song"     # why a song matched a YouTube video
py -m app.catalog audit-youtube                  # re-check every stored match
```

`match-report` is read only. It runs one YouTube search and prints every
candidate it found, whether each one was accepted or rejected and why, and which
one would be picked. Handy when a song ends up with a view count that looks
wrong. It does not change anything.

Then start the server:

```bash
uvicorn app.main:app --reload
```

Frontend, in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The game runs at http://localhost:5173.

If you just want to try it without any API keys, set `SONG_PROVIDER=mock`,
`POPULARITY_PROVIDER=mock`, and `AUDIO_PROVIDER=mock` in `.env` and run
`py scripts/generate_mock_audio.py`. That runs the whole game on made up songs.

## Current Status

Audidle is still in development. The game itself works end to end, and so does
the song catalog, which currently holds 89 songs with their MusicBrainz and
Spotify identities, ISRCs, genres, and difficulty tiers.

The main thing I am still working on is audio. Spotify does not give you
playable audio through their API, so the catalog knows about songs it cannot
actually play yet. For now I test with generated placeholder audio, and songs
without real audio are automatically kept out of the game. After that, the next
step is growing the song library.

Genre and decade filters and a "main hook" start mode are planned but not built
yet.

## A Couple of Notes

The answer stays on the server. Nothing identifying the song is sent to the
browser until the round ends, including the audio file name.

Songs have their own Audidle IDs instead of Spotify IDs, and MusicBrainz and
ISRCs are used to match the same recording across services. That way swapping
out a provider later does not mean rewriting the game.

## Inspiration

Audidle was inspired by OhnePixel's music guessing game streams.
