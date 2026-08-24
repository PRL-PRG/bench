# -------------
# check
# -------------
.PHONY: check lint check-type test
check: lint check-types test

lint:
	uv run --extra lint ruff check .

check-types:
	uv run --extra lint pyright

test:
	uv run --extra dev python -m pytest

# -------------
# act
# -------------
.PHONY: format fix-lint
format:
	uv run --extra lint ruff format .

fix-lint:
	uv run --extra lint ruff check --fix .

# -------------
# docs
# -------------
.PHONY: docs docs-serve
docs:
	uv run --group docs mkdocs build --strict

docs-serve:
	uv run --group docs mkdocs serve

