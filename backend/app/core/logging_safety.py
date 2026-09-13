"""Keep credentials out of logs.

httpx logs every request at INFO level as a full URL, and the YouTube API takes
its key as a query parameter. That combination printed a live API key into a
terminal during debugging, which is exactly the kind of leak that is invisible
until it has already happened.

Two defences, because either alone is fragile:

  - The httpx request logger is quietened, since its URLs are the direct source.
  - A filter scrubs anything that still looks like a credential from every log
    record, wherever it came from.
"""

import logging
import re

# Query parameters whose values must never reach a log.
SECRET_PARAMS = ("key", "api_key", "apikey", "access_token", "token", "client_secret")

_SECRET_QUERY = re.compile(
    r"(?i)\b(" + "|".join(SECRET_PARAMS) + r")=([^&\s\"']+)"
)
# Google API keys have a recognisable shape, so they are redacted even when they
# appear outside a query string.
_GOOGLE_KEY = re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")

REDACTED = "***redacted***"


def scrub(text: str) -> str:
    """Replace anything that looks like a credential."""
    text = _SECRET_QUERY.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    return _GOOGLE_KEY.sub(REDACTED, text)


class RedactingFilter(logging.Filter):
    """Scrubs credentials from a log record before it is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: scrub(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    scrub(value) if isinstance(value, str) else value
                    for value in record.args
                )
        return True


def install_log_redaction() -> None:
    """Quieten request logging and scrub what remains.

    Called from anything that talks to a credentialed API.
    """
    # httpx logs the full request URL, key included, at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    redactor = RedactingFilter()
    root = logging.getLogger()
    if not any(isinstance(existing, RedactingFilter) for existing in root.filters):
        root.addFilter(redactor)
    for handler in root.handlers:
        if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            handler.addFilter(redactor)
