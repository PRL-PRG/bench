.PHONY: check check-types test docs docs-serve format

check: check-types test

format:
	uv run ruff format .

check-types:
	uv run pyright

test:
	uv run python -m pytest

docs:
	uv run --group docs mkdocs build --strict

docs-serve:
	uv run --group docs mkdocs serve


