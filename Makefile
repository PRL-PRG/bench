.PHONY: check check-types test

check: check-types test

check-types:
	uv run pyright

test:
	uv run python -m pytest


