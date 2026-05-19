"""CLI: benchr bench / compare / show."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def _run(*args, env_extra: dict | None = None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "benchr", *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_bench_simple_command():
    r = _run("bench", "--runs", "2", "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout


def test_bench_two_commands():
    r = _run("bench", "--runs", "2", "sleep 0.01", "sleep 0.02")
    assert r.returncode == 0, r.stderr
    assert "sleep 0.01" in r.stdout
    assert "sleep 0.02" in r.stdout


def test_bench_writes_json(tmp_path: Path):
    out = tmp_path / "out.json"
    r = _run("bench", "--runs", "2", "--json", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    assert out.exists()
    data = json.loads(out.read_text())
    assert "samples" in data and len(data["samples"]) >= 2


def test_bench_writes_csv(tmp_path: Path):
    out = tmp_path / "out.csv"
    r = _run("bench", "--runs", "2", "--csv", str(out), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    lines = out.read_text().splitlines()
    assert lines[0].startswith("suite,benchmark")
    assert len(lines) >= 3  # header + 2 samples


def test_compare_subcommand(tmp_path: Path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _run("bench", "--runs", "2", "--json", str(a), "sleep 0.01")
    _run("bench", "--runs", "2", "--json", str(b), "sleep 0.01")
    r = _run("compare", str(a), str(b))
    assert r.returncode == 0, r.stderr
    assert "Summary" in r.stdout or "better" in r.stdout or "worse" in r.stdout


def test_show_subcommand(tmp_path: Path):
    out = tmp_path / "out.json"
    _run("bench", "--runs", "2", "--json", str(out), "sleep 0.01")
    r = _run("show", str(out))
    assert r.returncode == 0, r.stderr
    assert "elapsed" in r.stdout


def test_bench_compare_warns_or_diffs(tmp_path: Path):
    base = tmp_path / "base.json"
    _run("bench", "--runs", "2", "--json", str(base), "sleep 0.01")
    r = _run("bench", "--runs", "2", "--compare", str(base), "sleep 0.01")
    assert r.returncode == 0, r.stderr
    # Should print the per-benchmark comparison block.
    assert "Summary" in r.stdout or "geometric mean" in r.stdout or "better" in r.stdout or "worse" in r.stdout


def test_show_missing_file_errors(tmp_path: Path):
    r = _run("show", str(tmp_path / "nope.json"))
    assert r.returncode == 1
    assert "not found" in r.stderr
