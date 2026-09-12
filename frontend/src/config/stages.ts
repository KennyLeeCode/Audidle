/**
 * Fallback stage ladder.
 *
 * The real values come from GET /api/config so the backend stays the single
 * source of truth. This exists only so the shell can render something during
 * the first paint, before that request resolves. If these ever disagree with
 * the server, the server wins.
 */

import type { StageConfig } from '../types/config'

export const FALLBACK_STAGES: StageConfig[] = [
  { index: 0, duration: 0.01, label: '0.01s', fade_ms: 1.5 },
  { index: 1, duration: 0.1, label: '0.1s', fade_ms: 8 },
  { index: 2, duration: 0.5, label: '0.5s', fade_ms: 8 },
  { index: 3, duration: 2, label: '2s', fade_ms: 8 },
  { index: 4, duration: 8, label: '8s', fade_ms: 8 },
  { index: 5, duration: 15, label: '15s', fade_ms: 8 },
]
