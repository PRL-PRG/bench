.PHONY: check lint check-types test docs docs-serve format schema

check: lint check-types test

lint:
	uv run --extra dev ruff check .

format:
	uv run --extra dev ruff format .

check-types:
	uv run --extra dev pyright

test:
	uv run --extra dev python -m pytest

docs:
	uv run --group docs mkdocs build --strict

docs-serve:
	uv run --group docs mkdocs serve

schema:
	uv run --group schema python scripts/generate_schema.py


