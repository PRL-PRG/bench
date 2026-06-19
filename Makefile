.PHONY: check check-types test docs docs-serve

check: check-types test

check-types:
	uv run pyright

test:
	uv run python -m pytest

docs:
	uv run --group docs mkdocs build --strict

docs-serve:
	uv run --group docs mkdocs serve


