/**
 * The horizontal stage ladder above the play button.
 *
 * Renders one segment per stage from the server config, so adding or retiming a
 * stage needs no change here. Three visual states, as specified: completed
 * stages are filled but muted, the current stage is bright, and locked stages
 * stay dark.
 */

import type { StageConfig } from '../../types/config'
import './StageProgress.css'

interface StageProgressProps {
  stages: StageConfig[]
  currentIndex: number
  /** Dimmed once the round is over, since progress no longer means anything. */
  inactive?: boolean
}

export function StageProgress({ stages, currentIndex, inactive = false }: StageProgressProps) {
  return (
    <div
      className={`stage-progress ${inactive ? 'stage-progress--inactive' : ''}`}
      role="progressbar"
      aria-valuemin={1}
      aria-valuemax={stages.length}
      aria-valuenow={currentIndex + 1}
      aria-label={`Stage ${currentIndex + 1} of ${stages.length}`}
    >
      {stages.map((stage) => {
        const state =
          stage.index < currentIndex
            ? 'completed'
            : stage.index === currentIndex
              ? 'current'
              : 'locked'
        return (
          <div
            key={stage.index}
            className={`stage-progress__segment stage-progress__segment--${state}`}
            title={stage.label}
          />
        )
      })}
    </div>
  )
}
