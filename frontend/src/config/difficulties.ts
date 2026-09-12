/**
 * Fallback difficulty tiers and the accent palette.
 *
 * As with stages, the authoritative values arrive from GET /api/config. The
 * colours are duplicated here because the CSS needs them for first paint, and
 * they are applied at runtime as the `--accent` custom property rather than as
 * conditional class names.
 */

import type { Difficulty } from '../types/game'
import type { DifficultyConfig } from '../types/config'

export const DIFFICULTY_ORDER: Difficulty[] = [
  'easy',
  'medium',
  'hard',
  'expert',
  'impossible',
]

export const ACCENTS: Record<Difficulty, string> = {
  easy: '#1ed760',
  medium: '#f5c344',
  hard: '#f08a3c',
  expert: '#e5484d',
  impossible: '#a06bd8',
}

export const FALLBACK_DIFFICULTIES: DifficultyConfig[] = [
  {
    key: 'easy',
    label: 'Easy',
    color: ACCENTS.easy,
    description: 'Household names.',
    stream_floor: 1_000_000_000,
    stream_ceiling: null,
  },
  {
    key: 'medium',
    label: 'Medium',
    color: ACCENTS.medium,
    description: 'Widely known hits.',
    stream_floor: 500_000_000,
    stream_ceiling: 1_000_000_000,
  },
  {
    key: 'hard',
    label: 'Hard',
    color: ACCENTS.hard,
    description: 'Familiar if you follow the genre.',
    stream_floor: 100_000_000,
    stream_ceiling: 500_000_000,
  },
  {
    key: 'expert',
    label: 'Expert',
    color: ACCENTS.expert,
    description: 'Deep cuts and smaller artists.',
    stream_floor: 25_000_000,
    stream_ceiling: 100_000_000,
  },
  {
    key: 'impossible',
    label: 'Impossible',
    color: ACCENTS.impossible,
    description: 'Obscure.',
    stream_floor: 0,
    stream_ceiling: 25_000_000,
  },
]
