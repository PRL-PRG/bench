"""`python -m bench` entry point."""

import sys

from bench.cli import main


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()
