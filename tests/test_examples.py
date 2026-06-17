"""Smoke-test the bundled examples.

Each top-level ``examples/*.py`` builds its suite at module import time, so
importing it exercises the construction path (metrics, policies, matrix,
factories) without running any benchmark — the ``run(...)`` calls sit behind
``if __name__ == "__main__"``. This catches breakage like a custom Metric that
can't instantiate. The ``workloads/`` helpers and the ``external/`` examples
(which need real binaries) are intentionally excluded.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_FILES = sorted(EXAMPLES.glob("*.py"))


def test_examples_present():
    assert EXAMPLE_FILES, f"no example scripts found under {EXAMPLES}"


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_imports(path: Path):
    name = f"_example_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass under `from __future__ import annotations`
    # resolves string annotations via sys.modules[cls.__module__].
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
