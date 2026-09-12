/**
 * Volume slider with a percentage readout.
 *
 * Reports changes upward to useAudio, which applies them to the AudioPlayer.
 * Because both clip and full playback run through the same engine instance, one
 * setting covers both modes.
 */

import { VolumeIcon } from '../ui/Icons'
import './VolumeControl.css'

interface VolumeControlProps {
  /** 0 to 1. */
  volume: number
  onChange: (volume: number) => void
}

export function VolumeControl({ volume, onChange }: VolumeControlProps) {
  const percent = Math.round(volume * 100)

  return (
    <div className="volume">
      <div className="volume__header">
        <span className="volume__label">
          <VolumeIcon />
          Volume
        </span>
        <span className="volume__value">{percent}%</span>
      </div>
      <input
        className="volume__slider"
        type="range"
        min={0}
        max={100}
        value={percent}
        aria-label="Volume"
        /* The filled portion is drawn from this variable, since input[range]
           has no cross-browser way to style the track up to the thumb. */
        style={{ '--fill': `${percent}%` } as React.CSSProperties}
        onChange={(event) => onChange(Number(event.target.value) / 100)}
      />
    </div>
  )
}
