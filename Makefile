ifeq ($(PYTHON_NO_NATIVE),1)
LINT_FLAGS :=
else
LINT_FLAGS := --group lint
endif

# -------------
# check
# -------------
.PHONY: check lint typecheck test check-format
check: lint typecheck test

lint:
	uv run $(LINT_FLAGS) ruff check

typecheck:
	uv run $(LINT_FLAGS) pyright

test:
	uv run pytest

check-format:
	uv run $(LINT_FLAGS) ruff format --check

# -------------
# act
# -------------
.PHONY: format fix-lint
format:
	uv run $(LINT_FLAGS) ruff format

fix-lint:
	uv run $(LINT_FLAGS) ruff check --fix

# -------------
# docs
# -------------
.PHONY: docs docs-serve
docs:
	uv run --group docs mkdocs build --strict

docs-serve:
	uv run --group docs mkdocs serve

