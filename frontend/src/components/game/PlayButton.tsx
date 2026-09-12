/**
 * The large circular play button, the strongest element on the page.
 *
 * Owns no state. It reflects what the AudioPlayer is doing and reports presses
 * upward, which keeps every piece of audio logic out of the component tree.
 *
 * The ring around it fills over the clip duration while a clip is playing,
 * which gives the very short stages something visible. At 0.01s the sound is
 * over before it registers, so the ring is often the only confirmation the
 * player gets that anything happened.
 */

import { PlayIcon } from '../ui/Icons'
import './PlayButton.css'

interface PlayButtonProps {
  onPlay: () => void
  /** Seconds currently unlocked, shown beside the button. */
  durationLabel: string
  isPlaying: boolean
  isLoading: boolean
  disabled?: boolean
  /** Drives the ring sweep. */
  clipDuration: number
}

export function PlayButton({
  onPlay,
  durationLabel,
  isPlaying,
  isLoading,
  disabled = false,
  clipDuration,
}: PlayButtonProps) {
  return (
    <div className="play-row">
      <button
        type="button"
        className={`play-button ${isPlaying ? 'play-button--playing' : ''}`}
        onClick={onPlay}
        disabled={disabled || isLoading}
        aria-label={`Play ${durationLabel} of the song`}
      >
        <span
          className="play-button__ring"
          /* A CSS variable rather than a class, since the duration is a
             continuous value that comes from the server config. */
          style={{ '--clip-duration': `${clipDuration}s` } as React.CSSProperties}
          key={isPlaying ? 'on' : 'off'}
        />
        {isLoading ? <span className="spinner" /> : <PlayIcon size={40} />}
      </button>

      <span className="play-row__duration">
        {durationLabel.replace(/s$/, '')}
        <span className="play-row__unit">s</span>
      </span>
    </div>
  )
}
