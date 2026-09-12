/**
 * Bootstrap config, mirroring app/schemas/config.py.
 *
 * The client renders its stage pills and difficulty tabs from this rather than
 * from its own copy of the numbers. That is what makes retuning the game a
 * backend only change.
 */

import type { Difficulty } from './game'

export interface StageConfig {
  index: number
  /** Seconds of audio unlocked at this stage. */
  duration: number
  /** Pre-formatted label, for example "0.5s". Formatted server side so it
   *  cannot drift from the value. */
  label: string
  /** Volume ramp applied to each end of the clip, to avoid a click. Scales
   *  with the clip, so the 0.01s stage is not consumed by its own fade. */
  fade_ms: number
}

export interface DifficultyConfig {
  key: Difficulty
  label: string
  color: string
  description: string
  stream_floor: number
  stream_ceiling: number | null
}

export interface GameConfig {
  stages: StageConfig[]
  total_stages: number
  difficulties: DifficultyConfig[]
  auto_next_delay_seconds: number
  reroll_counts_as_loss: boolean
}
