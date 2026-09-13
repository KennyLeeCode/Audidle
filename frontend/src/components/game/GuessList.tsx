/**
 * The player's wrong guesses this round.
 *
 * Small, but it earns its place: without it the only feedback for a wrong guess
 * is the stage advancing, and players lose track of what they have already
 * tried. Shows guesses only, never anything about the answer.
 */

import type { LocalGuess } from '../../types/game'
import './GuessList.css'

interface GuessListProps {
  guesses: LocalGuess[]
}

export function GuessList({ guesses }: GuessListProps) {
  if (!guesses.length) return null

  return (
    <ul className="guess-list" aria-label="Your guesses">
      {guesses.map((guess, index) => (
        <li className="guess-list__item" key={`${guess.external_id}-${index}`}>
          <span className="guess-list__marker" aria-hidden="true" />
          <span className="guess-list__title">{guess.title}</span>
          <span className="guess-list__artist">{guess.artist}</span>
        </li>
      ))}
    </ul>
  )
}
