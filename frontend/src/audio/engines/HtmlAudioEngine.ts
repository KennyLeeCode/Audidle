/**
 * Fallback playback via an <audio> element.
 *
 * Used when the source cannot be decoded into a buffer, for example a long
 * stream or a format decodeAudioData rejects. It streams progressively instead
 * of requiring a full download, which is its one real advantage.
 *
 * It reports `precise = false`, and that is not pessimism. Clip boundaries here
 * are enforced by a timer, and between timer clamping and the delay before
 * play() produces sound the real duration lands within roughly 50 to 250ms of
 * the target. That is fine for the 8s and 15s stages and meaningless for the
 * 0.01s one. The UI reads this flag and tells the player, rather than silently
 * presenting an approximate clip as an exact one.
 */

import type { ClipRequest, PlaybackEngine } from '../types'

export class HtmlAudioEngine implements PlaybackEngine {
  readonly precise = false

  private element: HTMLAudioElement | null = null
  private stopTimer: number | null = null
  private volume = 1

  load(url: string): Promise<void> {
    this.dispose()
    const element = new Audio()
    element.preload = 'auto'
    element.volume = this.volume
    element.src = url
    this.element = element

    return new Promise((resolve, reject) => {
      const onReady = () => {
        cleanup()
        resolve()
      }
      const onError = () => {
        cleanup()
        reject(new Error('audio failed to load'))
      }
      const cleanup = () => {
        element.removeEventListener('canplaythrough', onReady)
        element.removeEventListener('error', onError)
      }
      element.addEventListener('canplaythrough', onReady)
      element.addEventListener('error', onError)
      element.load()
    })
  }

  async playClip({ duration, offsetMs }: ClipRequest): Promise<void> {
    const element = this.require()
    this.clearTimer()

    element.currentTime = offsetMs / 1000
    await element.play()

    // The imprecise part. Nothing better is available on this element, which is
    // exactly why WebAudioEngine is preferred whenever the source allows it.
    this.stopTimer = window.setTimeout(() => {
      element.pause()
      this.stopTimer = null
    }, duration * 1000)
  }

  async playFull(offsetMs: number): Promise<void> {
    const element = this.require()
    this.clearTimer()
    element.currentTime = offsetMs / 1000
    await element.play()
  }

  pause(): void {
    this.clearTimer()
    this.element?.pause()
  }

  async resume(): Promise<void> {
    await this.element?.play()
  }

  stop(): void {
    this.clearTimer()
    if (this.element) {
      this.element.pause()
      this.element.currentTime = 0
    }
  }

  setVolume(volume: number): void {
    this.volume = volume
    if (this.element) this.element.volume = volume
  }

  currentTime(): number {
    return this.element?.currentTime ?? 0
  }

  duration(): number | null {
    const value = this.element?.duration
    return value && Number.isFinite(value) ? value : null
  }

  dispose(): void {
    this.clearTimer()
    if (this.element) {
      this.element.pause()
      this.element.src = ''
      this.element = null
    }
  }

  private require(): HTMLAudioElement {
    if (!this.element) throw new Error('no audio loaded')
    return this.element
  }

  private clearTimer(): void {
    if (this.stopTimer !== null) {
      window.clearTimeout(this.stopTimer)
      this.stopTimer = null
    }
  }
}
