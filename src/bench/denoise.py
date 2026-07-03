"""Active system denoise (Linux + root), after ReBench's `denoise.py`.

`minimize()` sets the noisy knobs to their quiet values (CPU governor ->
performance, turbo off, perf_event_paranoid/-1, swappiness/0, ASLR off) and
saves the originals to a state file. `restore()` writes them back from that
file (so it is crash-safe and runnable standalone). Each knob is skipped unless
its file exists and is writable, so a missing knob or lack of privilege is
reported, never fatal, and the whole thing no-ops where the files are absent
(e.g. macOS).
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Generator
from pathlib import Path

from bench.utils import read_bracketed, read_text, write_text

# How to read a knob's current value (for save/status). Most are read verbatim.
# THP-style files (`a [b] c`) need the bracketed token extracted.
type Reader = Callable[[Path], str | None]

# Where the pre-minimize values are saved so `restore` can revert after the run
# (or after a crash, run standalone).
STATE_PATH = Path("/var/tmp/bench-denoise-state.json")


def _knobs(root: Path) -> list[tuple[Path, str, Reader]]:
    """(path, quiet-value, reader) for every knob that exists on this host."""
    cpu = root / "sys/devices/system/cpu"
    out: list[tuple[Path, str, Reader]] = [
        (p, "performance", read_text)
        for p in sorted(cpu.glob("cpu[0-9]*/cpufreq/scaling_governor"))
    ]
    no_turbo = cpu / "intel_pstate/no_turbo"
    boost = cpu / "cpufreq/boost"
    if no_turbo.exists():
        out.append((no_turbo, "1", read_text))
    elif boost.exists():
        out.append((boost, "0", read_text))
    out.append((root / "proc/sys/kernel/perf_event_paranoid", "-1", read_text))
    out.append((root / "proc/sys/vm/swappiness", "0", read_text))
    out.append((root / "proc/sys/kernel/randomize_va_space", "0", read_text))
    out.append(
        (root / "sys/kernel/mm/transparent_hugepage/enabled", "never", read_bracketed)
    )
    return [(p, v, r) for p, v, r in out if p.exists()]


def minimize(root: Path = Path("/"), state_path: Path = STATE_PATH) -> dict[str, str]:
    """Apply quiet values, saving originals to `state_path`. Returns what changed.

    Crash-safe: a leftover state file means a previous run never restored, so we
    revert it first (recovering the true originals). We persist the originals
    *before* mutating any knob, so a kill mid-apply is always recoverable via
    `restore`.
    """
    if state_path.exists():
        restore(state_path=state_path)

    knobs = _knobs(root)
    # Read every original first (reads change nothing). Skip unreadable knobs.
    saved: dict[str, str] = {}
    for path, _target, reader in knobs:
        current = reader(path)
        if current is not None:
            saved[str(path)] = current
    # Persist the undo-log up front: now any crash mid-apply is recoverable.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(saved, indent=2))
    # Then mutate.
    applied: dict[str, str] = {}
    for path, target, _reader in knobs:
        if str(path) in saved and write_text(path, target):
            applied[str(path)] = target
    return applied


def restore(state_path: Path = STATE_PATH) -> dict[str, str]:
    """Write the saved originals back and remove the state file."""
    if not state_path.exists():
        return {}
    saved: dict[str, str] = json.loads(state_path.read_text())
    restored = {p: v for p, v in saved.items() if write_text(Path(p), v)}
    state_path.unlink(missing_ok=True)
    return restored


def status(root: Path = Path("/")) -> dict[str, str | None]:
    """Current value of every present knob (no change)."""
    return {str(path): reader(path) for path, _, reader in _knobs(root)}


@contextlib.contextmanager
def denoise_session(
    root: Path = Path("/"), state_path: Path = STATE_PATH
) -> Generator[dict[str, str]]:
    """Minimize on enter, restore on exit (even on error)."""
    applied = minimize(root=root, state_path=state_path)
    try:
        yield applied
    finally:
        restore(state_path=state_path)


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0
