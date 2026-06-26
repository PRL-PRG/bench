"""Environment wiring: `bench doctor`, --check-environment, and the `run()` strategy."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bench import NoEnvironment, SystemEnvironment, bench, run, suite

REPO = Path(__file__).resolve().parents[1]

_NOT_ROOT = not (hasattr(os, "geteuid") and os.geteuid() == 0)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "bench", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_doctor_runs_and_reports():
    r = _run("doctor")
    assert r.returncode in (0, 1)  # 1 iff a high-severity issue is present
    assert "system" in r.stdout.lower()


def test_doctor_json_is_valid():
    r = _run("doctor", "--json")
    data = json.loads(r.stdout)
    assert data["system"] != ""


def test_run_check_environment_embeds_environment(tmp_path: Path):
    out = tmp_path / "o.json"
    r = _run(
        "run", "--check-environment", "--runs", "2", "--json", str(out), "sleep 0.01"
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text())
    assert data.get("environment") is not None
    assert "system" in data["environment"]


def test_run_omits_environment_by_default(tmp_path: Path):
    out = tmp_path / "o.json"
    r = _run("run", "--runs", "2", "--json", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text()).get("environment") is None


def test_run_check_environment_csv_has_comments(tmp_path: Path):
    out = tmp_path / "o.csv"
    r = _run(
        "run", "--check-environment", "--runs", "2", "--csv", str(out), "sleep 0.01"
    )
    assert r.returncode == 0, r.stderr
    lines = out.read_text().splitlines()
    assert lines[0].startswith("#")
    assert any(line.startswith("suite,benchmark") for line in lines)


def test_run_csv_has_no_comments_by_default(tmp_path: Path):
    out = tmp_path / "o.csv"
    r = _run("run", "--runs", "2", "--csv", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert out.read_text().splitlines()[0].startswith("suite,benchmark")


def _suite():
    return suite("s", bench("b").with_command(["true"]).with_runs(1))


def test_run_api_omits_environment_by_default():
    rep = run(_suite(), argv=["--no-progress"])
    assert rep.environment is None


def test_run_api_collects_with_system_environment():
    rep = run(_suite(), argv=["--no-progress"], environment=SystemEnvironment())
    assert rep.environment is not None


def test_run_api_no_environment_strategy():
    rep = run(_suite(), argv=["--no-progress"], environment=NoEnvironment())
    assert rep.environment is None


def test_denoise_status_runs():
    r = _run("denoise", "status")
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not _NOT_ROOT, reason="running as root")
def test_denoise_minimize_requires_root():
    r = _run("denoise", "minimize")
    assert r.returncode == 2
    assert "root" in (r.stdout + r.stderr).lower()


@pytest.mark.skipif(not _NOT_ROOT, reason="running as root")
def test_run_denoise_requires_root():
    r = _run("run", "--denoise", "--runs", "1", "sleep 0.01")
    assert r.returncode == 2
    assert "root" in (r.stdout + r.stderr).lower()
