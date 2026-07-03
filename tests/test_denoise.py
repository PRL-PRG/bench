"""Active denoise: minimize/restore/status against a (fake) sysfs tree.

The knobs are Linux sysfs/proc files. The functions are rooted so the logic is
exercised here against a writable fake tree (and naturally no-op where the
files are absent, e.g. on macOS).
"""

import json
from pathlib import Path

import pytest

import bench.denoise as denoise_mod
from bench.denoise import denoise_session, minimize, restore, status
from bench.utils import read_bracketed


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _fake_tree(root: Path) -> None:
    cpu = root / "sys/devices/system/cpu"
    _write(cpu / "cpu0/cpufreq/scaling_governor", "powersave\n")
    _write(cpu / "cpu1/cpufreq/scaling_governor", "powersave\n")
    _write(cpu / "intel_pstate/no_turbo", "0\n")
    _write(root / "proc/sys/vm/swappiness", "60\n")
    _write(root / "proc/sys/kernel/perf_event_paranoid", "2\n")
    _write(
        root / "sys/kernel/mm/transparent_hugepage/enabled", "always [madvise] never\n"
    )


def _read(path: Path) -> str:
    return path.read_text().strip()


def test_minimize_then_restore_roundtrip(tmp_path: Path):
    _fake_tree(tmp_path)
    state = tmp_path / "state.json"
    cpu = tmp_path / "sys/devices/system/cpu"

    thp = tmp_path / "sys/kernel/mm/transparent_hugepage/enabled"

    minimize(root=tmp_path, state_path=state)
    assert _read(cpu / "cpu0/cpufreq/scaling_governor") == "performance"
    assert _read(cpu / "cpu1/cpufreq/scaling_governor") == "performance"
    assert _read(cpu / "intel_pstate/no_turbo") == "1"
    assert _read(tmp_path / "proc/sys/vm/swappiness") == "0"
    assert _read(tmp_path / "proc/sys/kernel/perf_event_paranoid") == "-1"
    assert _read(thp) == "never"
    assert state.exists()

    restore(state_path=state)
    assert _read(cpu / "cpu0/cpufreq/scaling_governor") == "powersave"
    assert _read(tmp_path / "proc/sys/vm/swappiness") == "60"
    assert _read(tmp_path / "proc/sys/kernel/perf_event_paranoid") == "2"
    assert _read(thp) == "madvise"  # the bracketed token, not the raw line
    assert not state.exists()


def test_minimize_skips_absent_knobs(tmp_path: Path):
    state = tmp_path / "state.json"
    applied = minimize(root=tmp_path, state_path=state)  # empty tree
    assert applied == {}
    assert restore(state_path=state) == {}


def test_status_reads_present_knobs(tmp_path: Path):
    _fake_tree(tmp_path)
    st = status(root=tmp_path)
    assert any("scaling_governor" in k and v == "powersave" for k, v in st.items())


def test_session_restores_on_exception(tmp_path: Path):
    _fake_tree(tmp_path)
    state = tmp_path / "state.json"
    gov = tmp_path / "sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"

    with pytest.raises(RuntimeError):
        with denoise_session(root=tmp_path, state_path=state):
            assert _read(gov) == "performance"  # minimized inside the session
            raise RuntimeError("boom")

    assert _read(gov) == "powersave"  # restored despite the exception
    assert not state.exists()


def test_read_bracketed_extracts_selected_token(tmp_path: Path):
    p = tmp_path / "thp"
    p.write_text("always [madvise] never\n")
    assert read_bracketed(p) == "madvise"
    p.write_text("never\n")  # no brackets
    assert read_bracketed(p) is None
    assert read_bracketed(tmp_path / "missing") is None


def test_state_is_written_before_any_mutation(tmp_path: Path, monkeypatch):
    """Write-ahead: a crash during the apply loop still leaves a complete state
    file, so `restore` can recover. (Old code wrote the state file last.)"""
    _fake_tree(tmp_path)
    state = tmp_path / "state.json"
    swappiness = tmp_path / "proc/sys/vm/swappiness"
    thp = tmp_path / "sys/kernel/mm/transparent_hugepage/enabled"

    def boom(_path: Path, _value: str) -> bool:
        raise RuntimeError("crash mid-apply")

    monkeypatch.setattr(denoise_mod, "write_text", boom)
    with pytest.raises(RuntimeError):
        minimize(root=tmp_path, state_path=state)

    # The undo-log was persisted before the first knob write, with ALL originals.
    assert state.exists()
    saved = json.loads(state.read_text())
    assert saved[str(swappiness)] == "60"
    assert saved[str(thp)] == "madvise"
    # And nothing was actually mutated (the first write raised).
    assert _read(swappiness) == "60"


def test_minimize_self_heals_stale_state(tmp_path: Path):
    """A leftover state file (prior crash, knobs left minimized) is reverted
    before the fresh snapshot, so restore returns the TRUE originals."""
    _fake_tree(tmp_path)
    state = tmp_path / "state.json"
    gov = tmp_path / "sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"

    minimize(root=tmp_path, state_path=state)  # originals (powersave) saved
    assert _read(gov) == "performance"
    assert state.exists()  # simulate crash: state left, knobs still minimized

    minimize(root=tmp_path, state_path=state)  # must self-heal, not re-snapshot
    restore(state_path=state)
    assert _read(gov) == "powersave"  # true original, not the minimized value
