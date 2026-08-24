# -------------
# check
# -------------
.PHONY: check lint typecheck test check-format
check: lint typecheck test

lint:
	uv run --extra lint ruff check

typecheck:
	uv run --extra lint pyright

test:
	uv run --extra dev python -m pytest

check-format:
	uv run --extra lint ruff format --check

# -------------
# act
# -------------
.PHONY: format fix-lint
format:
	uv run --extra lint ruff format

fix-lint:
	uv run --extra lint ruff check --fix

# -------------
# docs
# -------------
.PHONY: docs docs-serve
docs:
	uv run --group docs mkdocs build --strict

docs-serve:
	uv run --group docs mkdocs serve

