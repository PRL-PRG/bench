.PHONY: check check-types test docs docs-serve format schema

check: check-types test

format:
	uv run ruff format .

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


