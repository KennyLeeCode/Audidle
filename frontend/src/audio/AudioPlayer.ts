/**
 * The audio facade the rest of the app talks to.
 *
 * Owns two things React should not: which engine is active, and the clip versus
 * full playback distinction. Components call playClip and playFullTrack and
 * never touch an AudioContext, which is the whole point of keeping this as a
 * plain class outside the component tree.
 *
 * Engine selection is the interesting part. The server says whether a source
 * supports precise clipping, this class trusts that as a first guess and then
 * verifies it by actually trying to decode. If decoding fails it falls back to
 * the streaming engine rather than leaving the player with no audio.
 */

import { HtmlAudioEngine } from './engines/HtmlAudioEngine'
import { WebAudioEngine } from './engines/WebAudioEngine'
import type { PlaybackEngine, PlaybackMode, PlaybackState } from './types'
import type { AudioSource } from '../types/song'

type Listener = (state: PlaybackState) => void

export class AudioPlayer {
  private engine: PlaybackEngine | null = null
  private listeners = new Set<Listener>()
  private volume = 1
  private mode: PlaybackMode = 'idle'
  private isPlaying = false
  private isReady = false
  private isLoading = false
  private error: string | null = null
  private tickHandle: number | null = null
  /** Guards against a slow load resolving after a newer one started. */
  private loadToken = 0

  // -- Subscription ---------------------------------------------------------

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    listener(this.snapshot())
    return () => this.listeners.delete(listener)
  }

  snapshot(): PlaybackState {
    return {
      mode: this.mode,
      isPlaying: this.isPlaying,
      isReady: this.isReady,
      isLoading: this.isLoading,
      precise: this.engine?.precise ?? true,
      currentTime: this.engine?.currentTime() ?? 0,
      duration: this.engine?.duration() ?? null,
      error: this.error,
    }
  }

  private emit(): void {
    const state = this.snapshot()
    this.listeners.forEach((listener) => listener(state))
  }

  // -- Loading --------------------------------------------------------------

  /**
   * Prepare a source so the first play press is instant.
   *
   * Called as soon as a round opens rather than on the first press, because
   * decoding takes long enough to be felt, and a stuttering first clip at the
   * 0.01s stage would be indistinguishable from a bug.
   */
  async prepare(source: AudioSource, url: string): Promise<void> {
    const token = ++this.loadToken
    this.teardownEngine()

    this.isLoading = true
    this.isReady = false
    this.error = null
    this.mode = 'idle'
    this.isPlaying = false
    this.emit()

    const engine = source.supports_precise_clips
      ? new WebAudioEngine()
      : new HtmlAudioEngine()

    try {
      await engine.load(url)
      // A newer round started while this was loading. Discard this one rather
      // than letting it overwrite the current engine.
      if (token !== this.loadToken) {
        engine.dispose()
        return
      }
      this.engine = engine
      this.isReady = true
    } catch {
      engine.dispose()
      if (token !== this.loadToken) return

      // Decoding failed despite the server saying it should work. Rather than
      // leaving the round silent, fall back to the streaming engine and let
      // the UI report that clip timing is now approximate.
      if (source.supports_precise_clips) {
        const fallback = new HtmlAudioEngine()
        try {
          await fallback.load(url)
          if (token !== this.loadToken) {
            fallback.dispose()
            return
          }
          this.engine = fallback
          this.isReady = true
        } catch {
          fallback.dispose()
          this.error = 'This track could not be loaded'
        }
      } else {
        this.error = 'This track could not be loaded'
      }
    } finally {
      if (token === this.loadToken) {
        this.isLoading = false
        this.emit()
      }
    }
  }

  // -- Playback -------------------------------------------------------------

  /**
   * Play the currently unlocked amount, always from the round's start point.
   *
   * Clips never resume where the last one stopped. Every press replays the same
   * prefix, which is what makes replaying a stage free.
   */
  async playClip(duration: number, offsetMs: number, fadeMs: number): Promise<void> {
    if (!this.engine) return
    this.error = null
    this.mode = 'clip'
    this.isPlaying = true
    this.emit()

    try {
      await this.engine.playClip({ duration, offsetMs, fadeMs })
    } catch {
      this.error = 'Playback failed'
      this.isPlaying = false
      this.emit()
      return
    }

    // The clip is scheduled to stop on its own, so the UI state is flipped back
    // on a matching timer rather than by polling the engine.
    window.setTimeout(
      () => {
        if (this.mode === 'clip') {
          this.isPlaying = false
          this.emit()
        }
      },
      duration * 1000 + 60,
    )
  }

  /** Play the track normally, for the reveal screen. */
  async playFullTrack(offsetMs = 0): Promise<void> {
    if (!this.engine) return
    this.error = null
    this.mode = 'full'
    this.isPlaying = true
    this.emit()

    try {
      await this.engine.playFull(offsetMs)
      this.startTicking()
    } catch {
      this.error = 'Playback failed'
      this.isPlaying = false
      this.emit()
    }
  }

  pause(): void {
    this.engine?.pause()
    this.isPlaying = false
    this.stopTicking()
    this.emit()
  }

  async resume(): Promise<void> {
    if (!this.engine) return
    await this.engine.resume()
    this.isPlaying = true
    this.startTicking()
    this.emit()
  }

  stop(): void {
    this.engine?.stop()
    this.isPlaying = false
    this.mode = 'idle'
    this.stopTicking()
    this.emit()
  }

  setVolume(volume: number): void {
    this.volume = Math.min(1, Math.max(0, volume))
    // Applies to both clip and full playback, since both run through the same
    // engine instance.
    this.engine?.setVolume(this.volume)
  }

  getVolume(): number {
    return this.volume
  }

  /** Drop the current source entirely, for example on Next Song. */
  reset(): void {
    this.loadToken += 1
    this.teardownEngine()
    this.mode = 'idle'
    this.isPlaying = false
    this.isReady = false
    this.isLoading = false
    this.error = null
    this.emit()
  }

  dispose(): void {
    this.stopTicking()
    this.teardownEngine()
    this.listeners.clear()
  }

  // -- Internal -------------------------------------------------------------

  private teardownEngine(): void {
    this.stopTicking()
    this.engine?.dispose()
    this.engine = null
  }

  /** Drive the reveal screen's progress bar. Only runs during full playback. */
  private startTicking(): void {
    this.stopTicking()
    this.tickHandle = window.setInterval(() => {
      const duration = this.engine?.duration()
      if (duration && this.engine!.currentTime() >= duration) {
        this.isPlaying = false
        this.stopTicking()
      }
      this.emit()
    }, 250)
  }

  private stopTicking(): void {
    if (this.tickHandle !== null) {
      window.clearInterval(this.tickHandle)
      this.tickHandle = null
    }
  }
}
