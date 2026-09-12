/**
 * The brief win or loss animation between the guess and the reveal.
 *
 * This is the only place the client-only `won` and `failed` statuses are used.
 * The server goes straight from playing to revealing, and this holds the screen
 * for a moment so the outcome lands before the answer appears.
 */

import './ResultFlash.css'

interface ResultFlashProps {
  won: boolean
}

export function ResultFlash({ won }: ResultFlashProps) {
  return (
    <div className={`result-flash ${won ? 'result-flash--won' : 'result-flash--failed'}`}>
      <div className="result-flash__mark" aria-hidden="true">
        {won ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="m4 12.5 5.5 5.5L20 7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M12 7v7" strokeLinecap="round" />
            <circle cx="12" cy="17.5" r="1.2" fill="currentColor" stroke="none" />
          </svg>
        )}
      </div>
      <p className="result-flash__label" role="status">
        {won ? 'Correct' : 'Song revealed'}
      </p>
    </div>
  )
}
