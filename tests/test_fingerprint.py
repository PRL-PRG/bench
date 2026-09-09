"""Fingerprint collection strategies.

A `Fingerprint` is a `key -> value` mapping; a fact the probe could not read is
absent, never `None`. `SystemProbe` reads platform facts, `NoProbe` is the off
switch, `GitProbe`/`BenchVersionProbe` record provenance and `CompositeProbe`
merges probes. Linux probes read a sysfs/proc tree (here a fake one under
tmp_path). macOS probes go through an injected command runner.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bench.core.fingerprint import (
    BenchVersionProbe,
    CompositeProbe,
    Fingerprint,
    GitProbe,
    NoProbe,
    Probe,
    SystemProbe,
)
from bench.core.fingerprint.system.linux import collect_linux
from bench.core.fingerprint.system.macos import collect_macos

needs_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="requires a git binary"
)


class _Canned(Probe):
    def __init__(self, fingerprint: Fingerprint | None) -> None:
        self.fingerprint = fingerprint

    def collect(self) -> Fingerprint | None:
        return self.fingerprint


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _fake_linux_tree(root: Path) -> None:
    cpu = root / "sys/devices/system/cpu"
    _write(cpu / "cpu0/cpufreq/scaling_governor", "powersave\n")
    _write(cpu / "cpu1/cpufreq/scaling_governor", "performance\n")
    _write(cpu / "intel_pstate/no_turbo", "0\n")  # turbo on
    _write(cpu / "smt/control", "on\n")
    _write(
        root / "sys/kernel/mm/transparent_hugepage/enabled", "always [madvise] never\n"
    )
    _write(root / "proc/sys/kernel/randomize_va_space", "2\n")
    _write(root / "proc/sys/vm/swappiness", "60\n")
    _write(root / "proc/sys/kernel/perf_event_paranoid", "2\n")
    _write(
        root / "proc/swaps",
        "Filename\tType\t\tSize\tUsed\tPriority\n"
        "/dev/sda2\tpartition\t8000000\t1024\t-2\n",
    )
    _write(
        root / "proc/cpuinfo",
        "processor\t: 0\nmodel name\t: Test CPU\n\nprocessor\t: 1\nmodel name\t: Test CPU\n",
    )
    _write(root / "sys/class/power_supply/AC/online", "1\n")


def _git_repo(root: Path) -> None:
    # A throwaway identity and config, so the user's own git setup (hooks,
    # signing, templates) cannot reach into the fixture.
    env = os.environ | {
        "HOME": str(root),
        "GIT_CONFIG_GLOBAL": str(root / "gitconfig"),
        "GIT_CONFIG_SYSTEM": str(root / "gitconfig"),
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, env=env
        )

    git("init", "-q")
    (root / "file.txt").write_text("hello\n")
    git("add", "file.txt")
    git("commit", "-qm", "initial")


def test_no_probe_collects_nothing():
    assert NoProbe().collect() is None


def test_from_optional_drops_the_facts_that_could_not_be_read():
    fp = Fingerprint.from_optional(system="Linux", cpu_model=None, logical_cpus=0)
    assert fp == Fingerprint(
        {"system": "Linux", "logical_cpus": 0}
    )  # 0 is a fact, None is not


def test_composite_probe_merges_snapshots_later_probe_winning():
    fp = CompositeProbe(
        _Canned(Fingerprint({"system": "Linux", "hostname": "a"})),
        _Canned(Fingerprint({"hostname": "b", "git commit": "abc"})),
    ).collect()
    assert fp == Fingerprint({"system": "Linux", "hostname": "b", "git commit": "abc"})


def test_composite_probe_collects_nothing_when_every_member_does():
    assert CompositeProbe(NoProbe(), NoProbe()).collect() is None
    assert CompositeProbe().collect() is None


@needs_git
def test_git_probe_records_the_head_commit(tmp_path: Path):
    _git_repo(tmp_path)
    fp = GitProbe(tmp_path).collect()
    assert fp is not None
    commit = fp["git commit"]
    assert len(commit) == 40 and not commit.endswith("(dirty)")


@needs_git
def test_git_probe_marks_a_dirty_tree(tmp_path: Path):
    _git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("changed\n")
    fp = GitProbe(tmp_path).collect()
    assert fp is not None
    assert fp["git commit"].endswith(" (dirty)")


@needs_git
def test_git_probe_collects_nothing_outside_a_repository(tmp_path: Path):
    probe = GitProbe(tmp_path, key="k", allow_failure=True)
    assert probe.collect() is None


@needs_git
def test_git_probe_raises_outside_a_repository_by_default(tmp_path: Path):
    # A probe that silently records nothing hides a mis-pointed folder, so the
    # failure is opt-in via allow_failure.
    with pytest.raises(ValueError, match="Git failed to run"):
        GitProbe(tmp_path, key="k").collect()


def test_git_probe_key_is_configurable(tmp_path: Path):
    assert GitProbe(tmp_path, key="bench commit", git_binary="/bin/true").key == (
        "bench commit"
    )


def test_bench_version_probe_reports_a_version():
    fp = BenchVersionProbe().collect()
    assert fp is not None
    assert fp["bench version"]


def test_collect_linux_parses_sysfs(tmp_path: Path):
    _fake_linux_tree(tmp_path)
    fp = collect_linux(root=tmp_path)
    assert fp["governors"] == ["performance", "powersave"]
    assert fp["turbo_enabled"] is True
    assert fp["aslr"] == 2
    assert fp["swappiness"] == 60
    assert fp["perf_event_paranoid"] == 2
    assert fp["transparent_hugepage"] == "madvise"
    assert fp["smt_enabled"] is True
    assert fp["swap_in_use"] is True
    assert fp["on_battery"] is False
    assert fp["cpu_model"] == "Test CPU"


def test_collect_linux_turbo_via_boost(tmp_path: Path):
    # No intel_pstate: cpufreq/boost=1 means turbo enabled.
    _write(tmp_path / "sys/devices/system/cpu/cpufreq/boost", "1\n")
    fp = collect_linux(root=tmp_path)
    assert fp["turbo_enabled"] is True


def test_collect_linux_unreadable_knobs_are_absent(tmp_path: Path):
    fp = collect_linux(root=tmp_path)
    for knob in (
        "governors",
        "turbo_enabled",
        "aslr",
        "swappiness",
        "perf_event_paranoid",
        "transparent_hugepage",
        "on_battery",
    ):
        assert knob not in fp
    # Common facts are still present.
    assert fp["system"] != ""
    assert fp["python_version"] != ""


def test_collect_macos_parses_sysctl():
    canned = {
        ("sysctl", "-n", "machdep.cpu.brand_string"): "Apple M2",
        ("sysctl", "-n", "hw.physicalcpu"): "8",
        ("sysctl", "-n", "hw.logicalcpu"): "8",
        ("sysctl", "-n", "vm.swapusage"): (
            "total = 2048.00M  used = 512.00M  free = 1536.00M  (encrypted)"
        ),
        ("pmset", "-g", "batt"): (
            "Now drawing from 'Battery Power'\n -InternalBattery-0 95%; discharging;"
        ),
        ("pmset", "-g"): " lowpowermode         1\n hibernatemode        3\n",
    }

    def run(cmd: list[str]) -> str | None:
        return canned.get(tuple(cmd))

    fp = collect_macos(run=run)
    assert fp["cpu_model"] == "Apple M2"
    assert fp["logical_cpus"] == 8
    assert fp["physical_cpus"] == 8
    assert fp["smt_enabled"] is False
    assert fp["swap_in_use"] is True
    assert fp["on_battery"] is True
    assert fp["low_power_mode"] is True


def test_collect_macos_missing_values_are_absent():
    # sysctl/pmset-only facts drop out; logical_cpus keeps its os.cpu_count() base.
    fp = collect_macos(run=lambda _cmd: None)
    for key in ("cpu_model", "physical_cpus", "on_battery", "low_power_mode"):
        assert key not in fp
    assert fp["system"] != ""
    assert fp["logical_cpus"] > 0


def test_system_probe_smoke():
    fp = SystemProbe().collect()
    assert fp is not None
    assert fp["system"] != ""
    assert fp["hostname"] != ""
