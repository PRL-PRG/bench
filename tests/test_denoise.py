"""Active denoise: minimize/restore/status against a (fake) sysfs tree.

The knobs are Linux sysfs/proc files; the functions are rooted so the logic is
exercised here against a writable fake tree (and naturally no-op where the
files are absent, e.g. on macOS).
"""

from pathlib import Path

import pytest

from bench.denoise import denoise_session, minimize, restore, status


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


def _read(path: Path) -> str:
    return path.read_text().strip()


def test_minimize_then_restore_roundtrip(tmp_path: Path):
    _fake_tree(tmp_path)
    state = tmp_path / "state.json"
    cpu = tmp_path / "sys/devices/system/cpu"

    minimize(root=tmp_path, state_path=state)
    assert _read(cpu / "cpu0/cpufreq/scaling_governor") == "performance"
    assert _read(cpu / "cpu1/cpufreq/scaling_governor") == "performance"
    assert _read(cpu / "intel_pstate/no_turbo") == "1"
    assert _read(tmp_path / "proc/sys/vm/swappiness") == "0"
    assert _read(tmp_path / "proc/sys/kernel/perf_event_paranoid") == "-1"
    assert state.exists()

    restore(state_path=state)
    assert _read(cpu / "cpu0/cpufreq/scaling_governor") == "powersave"
    assert _read(tmp_path / "proc/sys/vm/swappiness") == "60"
    assert _read(tmp_path / "proc/sys/kernel/perf_event_paranoid") == "2"
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
