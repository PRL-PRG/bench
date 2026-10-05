"""Smoke-test the bundled examples.

Two layers, both parametrized over the top-level ``examples/*.py``:

  - **import** (`test_example_imports`): each example builds its suite at module
    import time, so importing it exercises the construction path (metrics,
    policies, matrix, factories) without running any benchmark. Fast: catches
    breakage like a custom Metric that can't instantiate.
  - **run** (`test_example_runs`): each example is executed as a subprocess and
    must exit 0. This runs the benchmarks end to end, catching runtime breakage
    the import layer can't see. ``perf_cache_misses.py`` is skipped unless this
    is Linux with ``perf`` available.

The ``run`` layer only asserts a clean exit, not "zero failures": some examples
(``failure_handling.py``) record benchmark failures on purpose, and the process
still exits 0. The ``workloads/`` helpers and ``hyperfine_like.sh`` (a CLI-usage
snippet) are excluded.

The ``tutorial/`` scripts benchmark VMs this machine may not have, so they are
planned with ``--dry`` from their own directory instead; only 04 is measured, on
the interpreter running the tests.

The ``external/`` examples need real binaries, source trees or required path
flags, so each only has to build its CLI (``--help``); the one that needs
nothing external is also planned with ``--dry``.
"""

import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_FILES = sorted(EXAMPLES.glob("*.py"))
TUTORIAL = EXAMPLES / "tutorial"
TUTORIAL_FILES = sorted(TUTORIAL.glob("*.py"))
EXTERNAL = EXAMPLES / "external"
# sqlite_bench.py is the workload throughput_sqlite.py measures, not a bench app.
EXTERNAL_APPS = sorted(p for p in EXTERNAL.glob("*.py") if p.name != "sqlite_bench.py")

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
    # --no-progress keeps this layer's output readable, and an example that
    # configures no output sink of its own must still run under it.
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


# ----- tutorial/ --------------------------------------------------------------


def _run_tutorial(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, str(path), "--no-progress", *args],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=TUTORIAL,
    )
    assert proc.returncode == 0, (
        f"{path.name} exited {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    return proc


def _dry_commands(stdout: str) -> list[str]:
    """The backticked command of every `--dry` line."""
    return [ln.split("`")[1] for ln in stdout.splitlines() if "`" in ln]


def test_tutorials_present():
    assert TUTORIAL_FILES, f"no tutorial scripts found under {TUTORIAL}"


@pytest.mark.parametrize("path", TUTORIAL_FILES, ids=lambda p: p.name)
def test_tutorial_plans(path: Path):
    proc = _run_tutorial(path, "--dry")
    assert _dry_commands(proc.stdout), proc.stdout


def test_awfy_tutorial_harness_resolves_inside_its_cwd():
    # The cwd is the AWFY directory, so a harness path relative to it must not
    # repeat that directory.
    proc = _run_tutorial(TUTORIAL / "06_awfy.py", "--dry")
    for command in _dry_commands(proc.stdout):
        cd, cwd, sep, _vm, harness, *_ = shlex.split(command)
        assert (cd, sep) == ("cd", "&&"), command
        resolved = (TUTORIAL / cwd / harness).resolve()
        assert resolved.parent == (TUTORIAL / cwd).resolve(), command


@pytest.mark.skipif(
    shutil.which(f"python{sys.version_info.major}.{sys.version_info.minor}") is None,
    reason="needs a versioned python on PATH",
)
def test_custom_metric_tutorial_measures_every_suite(tmp_path: Path):
    vm = f"python{sys.version_info.major}.{sys.version_info.minor}"
    tutorial = TUTORIAL / "04_custom_metric.py"
    planned_vms = {
        shlex.split(c)[0]
        for c in _dry_commands(_run_tutorial(tutorial, "--dry").stdout)
    }
    if vm not in planned_vms:
        pytest.skip(f"{vm} is not in the tutorial's vm matrix {sorted(planned_vms)}")
    out = tmp_path / "r.json"
    _run_tutorial(
        tutorial,
        "--include",
        f"^example/.*vm={vm}",
        "--json",
        str(out),
    )
    executions = json.loads(out.read_text())["executions"]
    assert executions
    for e in executions:
        samples = e.get("process_samples", []) + [
            s for it in e.get("iterations", []) for s in it.get("samples", [])
        ]
        assert samples, f"{e['suite']}/{e['benchmark']} carries no samples"


# ----- external/ --------------------------------------------------------------


def _run_external(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, str(path), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=EXTERNAL,
    )
    assert proc.returncode == 0, (
        f"{path.name} exited {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    return proc


@pytest.mark.parametrize("path", EXTERNAL_APPS, ids=lambda p: p.name)
def test_external_builds_its_cli(path: Path):
    assert "usage:" in _run_external(path, "--help").stdout


def test_external_throughput_sqlite_plans():
    proc = _run_external(EXTERNAL / "throughput_sqlite.py", "--dry", "--no-progress")
    commands = _dry_commands(proc.stdout)
    assert commands and all("sqlite_bench.py" in c for c in commands)
