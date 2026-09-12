/**
 * The difficulty tabs above the player.
 *
 * Each tab carries its own accent as an inline custom property, so an unselected
 * tab still shows its tier colour on hover. Selecting one retints the whole
 * center column, because App sets --accent from the same source.
 */

import { Pill } from '../ui/Pill'
import type { DifficultyConfig } from '../../types/config'
import type { Difficulty } from '../../types/game'
import './DifficultyTabs.css'

interface DifficultyTabsProps {
  difficulties: DifficultyConfig[]
  selected: Difficulty
  onSelect: (difficulty: Difficulty) => void
  disabled?: boolean
}

export function DifficultyTabs({
  difficulties,
  selected,
  onSelect,
  disabled = false,
}: DifficultyTabsProps) {
  return (
    <div className="difficulty-tabs" role="tablist" aria-label="Difficulty">
      {difficulties.map((difficulty) => (
        <Pill
          key={difficulty.key}
          className="difficulty-tab"
          style={{ '--tab-accent': difficulty.color } as React.CSSProperties}
          active={difficulty.key === selected}
          disabled={disabled}
          onClick={() => onSelect(difficulty.key)}
          title={difficulty.description}
        >
          {difficulty.label}
        </Pill>
      ))}
    </div>
  )
}
