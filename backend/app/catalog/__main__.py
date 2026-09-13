"""Entry point so the CLI runs as `py -m app.catalog <command>`."""

import sys

from app.catalog.cli import main

raise SystemExit(main(sys.argv[1:]))
