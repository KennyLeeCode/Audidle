/**
 * The reveal screen.
 *
 * Shown after a round ends, and deliberately not in a hurry. The full track is
 * already playing by the time this mounts, and nothing advances until the
 * player presses Next Song, so this doubles as a listening screen rather than
 * an interstitial to click past.
 *
 * Owns only the transport controls for full playback. The result data is fetched
 * by useGame and passed down.
 */

import { Pill } from '../ui/Pill'
import { PlayIcon, SpotifyIcon } from '../ui/Icons'
import type { PlaybackState } from '../../audio/types'
import type { RoundResult } from '../../types/game'
import type { DifficultyConfig } from '../../types/config'
import './ResultView.css'

interface ResultViewProps {
  result: RoundResult
  difficultyConfig: DifficultyConfig | undefined
  playback: PlaybackState
  onTogglePlayback: () => void
  onNextSong: () => void
  /** Counts down when Auto Next is on, so the jump is never a surprise. */
  autoNextIn: number | null
}

function formatTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(whole / 60)
  return `${minutes}:${String(whole % 60).padStart(2, '0')}`
}

export function ResultView({
  result,
  difficultyConfig,
  playback,
  onTogglePlayback,
  onNextSong,
  autoNextIn,
}: ResultViewProps) {
  const { song, won } = result
  const progress =
    playback.duration && playback.duration > 0
      ? Math.min(100, (playback.currentTime / playback.duration) * 100)
      : 0

  return (
    <div className={`result ${won ? 'result--won' : 'result--failed'}`}>
      <p className="result__verdict">
        {won ? 'Correct' : 'You ran out of guesses'}
      </p>

      <div className="result__card">
        <div className="result__art">
          {song.artwork_url ? (
            <img src={song.artwork_url} alt={`${song.album ?? song.title} cover art`} />
          ) : (
            /* Artwork is often missing, so this is a designed state rather
               than a broken image. */
            <span className="result__art-fallback" aria-hidden="true">
              {song.title.charAt(0).toUpperCase()}
            </span>
          )}
        </div>

        <div className="result__meta">
          <h2 className="result__title">{song.title}</h2>
          <p className="result__artist">{song.artist}</p>
          {song.album && (
            <p className="result__album">
              {song.album}
              {song.release_year ? ` · ${song.release_year}` : ''}
            </p>
          )}

          <div className="result__tags">
            {difficultyConfig && (
              <span
                className="result__tag result__tag--difficulty"
                style={{ '--tag-accent': difficultyConfig.color } as React.CSSProperties}
              >
                {difficultyConfig.label}
              </span>
            )}
            <span className="result__tag">
              {result.stages_used} of {result.total_stages} stages
            </span>
            <span className="result__tag">Reached {result.duration_reached}s</span>
          </div>

          {song.external_url && (
            <a
              className="result__link"
              href={song.external_url}
              target="_blank"
              rel="noreferrer noopener"
            >
              <SpotifyIcon />
              Open in Spotify
            </a>
          )}
        </div>
      </div>

      {/* Full playback transport. The track is already playing when this mounts. */}
      <div className="result__transport">
        <button
          type="button"
          className="result__play"
          onClick={onTogglePlayback}
          aria-label={playback.isPlaying ? 'Pause' : 'Play'}
        >
          {playback.isPlaying ? (
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="6.5" y="5" width="3.5" height="14" rx="1.2" />
              <rect x="14" y="5" width="3.5" height="14" rx="1.2" />
            </svg>
          ) : (
            <PlayIcon size={20} />
          )}
        </button>

        <div className="result__scrubber">
          <div className="result__scrubber-fill" style={{ width: `${progress}%` }} />
        </div>

        <span className="result__time">
          {formatTime(playback.currentTime)}
          {playback.duration ? ` / ${formatTime(playback.duration)}` : ''}
        </span>
      </div>

      <Pill className="result__next" active onClick={onNextSong}>
        Next Song
        {autoNextIn !== null && <span className="result__countdown">{autoNextIn}</span>}
      </Pill>
    </div>
  )
}
