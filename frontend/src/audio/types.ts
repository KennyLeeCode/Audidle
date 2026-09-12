/**
 * Playback engine contract.
 *
 * Two engines implement this. Which one AudioPlayer picks decides whether the
 * shortest stages are exact or approximate, so the distinction is surfaced on
 * the interface rather than hidden inside it.
 */

export type PlaybackMode = 'idle' | 'clip' | 'full'

export interface ClipRequest {
  /** Seconds of audio to play, from the round's start offset. */
  duration: number
  /** Where in the track the clip begins. Always 0 for the from_start mode. */
  offsetMs: number
  /** Volume ramp on each end, to avoid the click a hard cut produces. */
  fadeMs: number
}

export interface PlaybackEngine {
  /**
   * Whether this engine can honour a requested clip duration exactly.
   *
   * False means clip boundaries land within roughly 50 to 250ms of the target,
   * which makes the sub second stages meaningless. The UI reads this so it can
   * say so rather than claiming a precision it is not delivering.
   */
  readonly precise: boolean

  /** Fetch and prepare the source. Resolves once playback can start instantly. */
  load(url: string): Promise<void>

  /** Play exactly `duration` seconds from the offset, then stop. */
  playClip(request: ClipRequest): Promise<void>

  /** Play the track normally from the offset, for the reveal screen. */
  playFull(offsetMs: number): Promise<void>

  pause(): void
  resume(): Promise<void>
  stop(): void
  setVolume(volume: number): void

  /** Seconds elapsed in the current playback, for the reveal progress bar. */
  currentTime(): number
  /** Total track length in seconds, or null if not yet known. */
  duration(): number | null

  dispose(): void
}

/** Emitted so React can render play state without owning any audio logic. */
export interface PlaybackState {
  mode: PlaybackMode
  isPlaying: boolean
  /** True once a source is loaded and playback can begin without delay. */
  isReady: boolean
  isLoading: boolean
  /** False when the active engine cannot honour short clip durations exactly. */
  precise: boolean
  currentTime: number
  duration: number | null
  error: string | null
}
