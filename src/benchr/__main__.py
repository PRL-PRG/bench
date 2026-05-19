"""``python -m benchr`` entry point."""

import sys

from benchr.cli import main


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()
