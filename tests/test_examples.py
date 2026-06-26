"""Smoke-test the bundled examples.

Two layers, both parametrized over the top-level ``examples/*.py``:

  - **import** (`test_example_imports`): each example builds its suite at module
    import time, so importing it exercises the construction path (metrics,
    policies, matrix, factories) without running any benchmark. Fast; catches
    breakage like a custom Metric that can't instantiate.
  - **run** (`test_example_runs`): each example is executed as a subprocess and
    must exit 0. This runs the benchmarks end to end, catching runtime breakage
    the import layer can't see. ``perf_cache_misses.py`` is skipped unless this
    is Linux with ``perf`` available.

The ``run`` layer only asserts a clean exit, not "zero failures": some examples
(``failure_handling.py``) record benchmark failures on purpose, and the process
still exits 0. The ``workloads/`` helpers, the ``external/`` examples (which need
real binaries), and ``hyperfine_like.sh`` (a CLI-usage snippet) are excluded.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_FILES = sorted(EXAMPLES.glob("*.py"))

PERF_EXAMPLE = "perf_cache_misses.py"


def _perf_available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("perf") is not None


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


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_runs(path: Path):
    if path.name == PERF_EXAMPLE and not _perf_available():
        pytest.skip("perf example requires Linux with perf available")
    proc = subprocess.run(
        [sys.executable, str(path), "--no-progress"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"{path.name} exited {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
