/**
 * Left column: difficulty selection and utility actions.
 *
 * Reroll sits here rather than next to Skip, on purpose. They do very different
 * things and putting them side by side would invite the wrong one. Skip keeps
 * the song and unlocks more of it, Reroll throws the round away and draws a
 * different song.
 */

import { Pill } from '../ui/Pill'
import { FilterIcon, HeartIcon, MessageIcon, ShuffleIcon } from '../ui/Icons'
import type { DifficultyConfig } from '../../types/config'
import type { Difficulty } from '../../types/game'
import './Sidebar.css'

interface SidebarProps {
  difficulties: DifficultyConfig[]
  selected: Difficulty
  onSelect: (difficulty: Difficulty) => void
  onReroll: () => void
  disabled?: boolean
  rerollCountsAsLoss: boolean
}

export function Sidebar({
  difficulties,
  selected,
  onSelect,
  onReroll,
  disabled = false,
  rerollCountsAsLoss,
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__logo">audidle</span>
      </div>

      <nav className="sidebar__group" aria-label="Difficulty">
        {difficulties.map((difficulty) => (
          <Pill
            key={difficulty.key}
            block
            className="sidebar__difficulty"
            style={{ '--tab-accent': difficulty.color } as React.CSSProperties}
            active={difficulty.key === selected}
            disabled={disabled}
            onClick={() => onSelect(difficulty.key)}
            title={difficulty.description}
          >
            {difficulty.label}
          </Pill>
        ))}
      </nav>

      <div className="sidebar__group sidebar__group--utility">
        <Pill
          block
          icon={<ShuffleIcon />}
          onClick={onReroll}
          disabled={disabled}
          title={
            rerollCountsAsLoss
              ? 'Abandon this song and draw another. Counts as a loss'
              : 'Abandon this song and draw another'
          }
        >
          New song
        </Pill>
        {/* Not yet wired. Disabled rather than present-but-dead, so it does not
            look like something that should work. */}
        <Pill block icon={<FilterIcon />} disabled title="Filters are not implemented yet">
          Filters
        </Pill>
        <Pill block icon={<MessageIcon />} disabled title="Not implemented yet">
          Feedback
        </Pill>
        <Pill block icon={<HeartIcon />} disabled title="Not implemented yet">
          Support
        </Pill>
      </div>
    </aside>
  )
}
