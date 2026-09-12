/**
 * Sample accurate playback via the Web Audio API.
 *
 * This engine is the reason the 0.01 second stage works at all.
 *
 * The naive approach, an <audio> element plus setTimeout, cannot do it. Timer
 * clamping, decode latency, and the unpredictable delay before play() actually
 * produces sound put the real result somewhere between 0 and 300ms, and vary it
 * between presses. At a 10ms target that is not a rounding error, it is the
 * entire clip.
 *
 * AudioBufferSourceNode.start(when, offset, duration) instead schedules against
 * the audio hardware clock at sample resolution. At 44.1kHz, 0.01s is exactly
 * 441 samples, and the browser honours that. The cost is that the whole buffer
 * must be decoded up front, which is why load() is awaited before the play
 * button becomes active.
 */

import type { ClipRequest, PlaybackEngine } from '../types'

/** Floor for the gain ramp, since exponential ramps cannot reach zero. */
const SILENCE = 0.0001

export class WebAudioEngine implements PlaybackEngine {
  readonly precise = true

  private context: AudioContext | null = null
  private gain: GainNode | null = null
  private buffer: AudioBuffer | null = null
  private source: AudioBufferSourceNode | null = null
  private volume = 1

  // Tracked so full playback can report progress and support pause/resume,
  // which a BufferSourceNode does not offer on its own.
  private startedAt = 0
  private startOffset = 0
  private pausedAt: number | null = null

  /**
   * Create the context lazily, on a user gesture.
   *
   * Browsers start an AudioContext suspended until a gesture resumes it.
   * Constructing it at module load would leave it permanently suspended.
   */
  private ensureContext(): AudioContext {
    if (!this.context) {
      this.context = new AudioContext()
      this.gain = this.context.createGain()
      this.gain.gain.value = this.volume
      this.gain.connect(this.context.destination)
    }
    return this.context
  }

  async load(url: string): Promise<void> {
    const response = await fetch(url)
    if (!response.ok) {
      throw new Error(`audio request failed with ${response.status}`)
    }
    const encoded = await response.arrayBuffer()
    const context = this.ensureContext()
    // decodeAudioData is what makes sample accurate scheduling possible, and
    // also what makes this engine unusable for DRM protected sources.
    this.buffer = await context.decodeAudioData(encoded)
  }

  async playClip({ duration, offsetMs, fadeMs }: ClipRequest): Promise<void> {
    const context = this.ensureContext()
    if (!this.buffer) throw new Error('no audio loaded')

    await this.unlock(context)
    this.stop()

    const offsetSeconds = offsetMs / 1000
    // Never run past the end of the track, which would throw.
    const available = Math.max(0, this.buffer.duration - offsetSeconds)
    const clipDuration = Math.min(duration, available)
    if (clipDuration <= 0) return

    const source = context.createBufferSource()
    source.buffer = this.buffer

    // A per clip gain node, so the envelope is discarded with the clip and
    // cannot bleed into the next press.
    const envelope = context.createGain()
    source.connect(envelope)
    envelope.connect(this.gain!)

    const startAt = context.currentTime
    // Fade never exceeds a third of the clip. Without this cap a fixed ramp
    // would consume most of the 0.01s stage.
    const fade = Math.min(fadeMs / 1000, clipDuration / 3)

    envelope.gain.setValueAtTime(SILENCE, startAt)
    envelope.gain.exponentialRampToValueAtTime(1, startAt + fade)
    envelope.gain.setValueAtTime(1, startAt + clipDuration - fade)
    envelope.gain.exponentialRampToValueAtTime(SILENCE, startAt + clipDuration)

    // The single line that makes the short stages exact. Both the offset and
    // the duration are honoured at sample resolution.
    source.start(startAt, offsetSeconds, clipDuration)
    source.stop(startAt + clipDuration)

    this.source = source
    this.startedAt = startAt
    this.startOffset = offsetSeconds
    this.pausedAt = null

    source.onended = () => {
      if (this.source === source) this.source = null
    }
  }

  async playFull(offsetMs: number): Promise<void> {
    const context = this.ensureContext()
    if (!this.buffer) throw new Error('no audio loaded')

    await this.unlock(context)
    this.stop()
    this.startFrom(offsetMs / 1000)
  }

  /** Start an unbounded playback from a given offset in seconds. */
  private startFrom(offsetSeconds: number): void {
    const context = this.context!
    const source = context.createBufferSource()
    source.buffer = this.buffer
    source.connect(this.gain!)
    source.start(context.currentTime, offsetSeconds)

    this.source = source
    this.startedAt = context.currentTime
    this.startOffset = offsetSeconds
    this.pausedAt = null

    source.onended = () => {
      if (this.source === source) this.source = null
    }
  }

  pause(): void {
    if (!this.context || !this.source) return
    // A BufferSourceNode cannot be paused, only stopped. Remembering where it
    // got to and starting a fresh node from there is what resume does.
    this.pausedAt = this.currentTime()
    this.source.onended = null
    this.source.stop()
    this.source = null
  }

  async resume(): Promise<void> {
    if (this.pausedAt === null) return
    const context = this.ensureContext()
    await this.unlock(context)
    this.startFrom(this.pausedAt)
  }

  stop(): void {
    if (this.source) {
      this.source.onended = null
      try {
        this.source.stop()
      } catch {
        // Already stopped. Stopping twice throws and is harmless.
      }
      this.source = null
    }
    this.pausedAt = null
  }

  setVolume(volume: number): void {
    this.volume = volume
    if (this.gain && this.context) {
      // A short ramp rather than a jump, so dragging the slider does not click.
      this.gain.gain.setTargetAtTime(volume, this.context.currentTime, 0.01)
    }
  }

  currentTime(): number {
    if (this.pausedAt !== null) return this.pausedAt
    if (!this.context || !this.source) return this.startOffset
    return this.startOffset + (this.context.currentTime - this.startedAt)
  }

  duration(): number | null {
    return this.buffer?.duration ?? null
  }

  dispose(): void {
    this.stop()
    this.buffer = null
    void this.context?.close()
    this.context = null
    this.gain = null
  }

  /** Resume a context the browser suspended until a user gesture. */
  private async unlock(context: AudioContext): Promise<void> {
    if (context.state === 'suspended') await context.resume()
  }
}
