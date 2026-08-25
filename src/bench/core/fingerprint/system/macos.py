from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Any

from bench.core.fingerprint.base import known
from bench.core.fingerprint.system.common import base
from bench.io import to_int


def _sysctl_run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def collect_macos(
    run: Callable[[list[str]], str | None] = _sysctl_run,
) -> dict[str, Any]:
    def sysctl(key: str) -> str | None:
        return run(["sysctl", "-n", key])

    physical = to_int(sysctl("hw.physicalcpu"))
    logical = to_int(sysctl("hw.logicalcpu"))
    smt = logical > physical if logical is not None and physical is not None else None
    batt = run(["pmset", "-g", "batt"])
    return base() | known(
        cpu_model=sysctl("machdep.cpu.brand_string"),
        logical_cpus=logical,
        physical_cpus=physical,
        smt_enabled=smt,
        swap_in_use=_macos_swap(sysctl("vm.swapusage")),
        on_battery=("Battery Power" in batt) if batt is not None else None,
        low_power_mode=_macos_low_power(run(["pmset", "-g"])),
    )


def _macos_swap(text: str | None) -> bool | None:
    if not text:
        return None
    m = re.search(r"used\s*=\s*([\d.]+)", text)
    return float(m.group(1)) > 0 if m else None


def _macos_low_power(text: str | None) -> bool | None:
    if not text:
        return None
    m = re.search(r"lowpowermode\s+(\d+)", text)
    return m.group(1) == "1" if m else None
