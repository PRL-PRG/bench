"""Active system denoise (Linux + root), after ReBench's `denoise.py`.

`minimize()` quiets the knobs below and saves the originals to a state file;
`restore()` writes them back from it, so it survives a crash and runs
standalone. A knob that is missing or unwritable is skipped and reported, never
fatal - so this no-ops where the files do not exist (e.g. macOS).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType

from bench.core.fingerprint.system.linux import (
    ASLR,
    CPU_DIR,
    CPUFREQ_BOOST,
    GOVERNOR_GLOB,
    NO_TURBO,
    PERF_EVENT_PARANOID,
    SWAPPINESS,
    THP_ENABLED,
)
from bench.io import read_bracketed, read_text, write_text

# How to read a knob's current value (for save/status). Most are read verbatim.
# THP-style files (`a [b] c`) need the bracketed token extracted.
type _Reader = Callable[[Path], str | None]

# Where the pre-minimize values are saved so `restore` can revert after the run
# (or after a crash, run standalone).
DENOISE_DEFAULT_STATE_PATH = Path("/var/tmp/bench-denoise-state.json")


class Denoise(AbstractContextManager[dict[str, str], None]):
    def __init__(
        self,
        *,
        state_path: Path = Path("/var/tmp/bench-denoise-state.json"),
        root: Path = Path("/"),
    ) -> None:
        self.root = root
        self.state_path = state_path

    def _knobs(self) -> list[tuple[Path, str, _Reader]]:
        """(path, quiet-value, reader) for every knob that exists on this host."""
        cpu = self.root / CPU_DIR
        proc = self.root / "proc"
        out: list[tuple[Path, str, _Reader]] = [
            (p, "performance", read_text) for p in sorted(cpu.glob(GOVERNOR_GLOB))
        ]
        no_turbo = cpu / NO_TURBO
        boost = cpu / CPUFREQ_BOOST
        if no_turbo.exists():
            out.append((no_turbo, "1", read_text))
        elif boost.exists():
            out.append((boost, "0", read_text))
        out.append((proc / PERF_EVENT_PARANOID, "-1", read_text))
        out.append((proc / SWAPPINESS, "0", read_text))
        out.append((proc / ASLR, "0", read_text))
        out.append((self.root / THP_ENABLED, "never", read_bracketed))
        return [(p, v, r) for p, v, r in out if p.exists()]

    def minimize(self) -> dict[str, str]:
        """Apply quiet values, saving originals to `state_path`. Returns what changed.

        Crash-safe: a leftover state file means a previous run never restored, so we
        revert it first (recovering the true originals). We persist the originals
        *before* mutating any knob, so a kill mid-apply is always recoverable via
        `restore`.
        """
        if self.state_path.exists():
            self.restore()

        knobs = self._knobs()
        # Read every original first (reads change nothing). Skip unreadable knobs.
        saved: dict[str, str] = {}
        for path, _target, reader in knobs:
            current = reader(path)
            if current is not None:
                saved[str(path)] = current
        # Persist the undo-log up front: now any crash mid-apply is recoverable.
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(saved, indent=2))
        # Then mutate.
        applied: dict[str, str] = {}
        for path, target, _reader in knobs:
            if str(path) in saved and write_text(path, target):
                applied[str(path)] = target
        return applied

    def restore(self) -> dict[str, str]:
        """Write the saved originals back and remove the state file."""
        if not self.state_path.exists():
            return {}
        saved: dict[str, str] = json.loads(self.state_path.read_text())
        restored = {p: v for p, v in saved.items() if write_text(Path(p), v)}
        self.state_path.unlink(missing_ok=True)
        return restored

    def status(self) -> dict[str, str | None]:
        """Current value of every present knob (no change)."""
        return {str(path): reader(path) for path, _, reader in self._knobs()}

    def __enter__(self) -> dict[str, str]:
        return self.minimize()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None:
        self.restore()
        return None


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0
