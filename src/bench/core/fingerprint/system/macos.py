from __future__ import annotations

import contextlib
import re
import subprocess
from collections.abc import Callable

from bench.core.fingerprint.system.common import UnixSystemEnvironment
from bench.io import to_int


class MacOSSystemEnvironment(UnixSystemEnvironment, total=False):
    low_power_mode: bool


def _sysctl_run(cmd: list[str]) -> str | None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()


def collect_macos(
    run: Callable[[list[str]], str | None] = _sysctl_run,
) -> MacOSSystemEnvironment:
    def sysctl(key: str) -> str | None:
        return run(["sysctl", "-n", key])

    env = MacOSSystemEnvironment()

    cpu_model = sysctl("machdep.cpu.brand_string")
    if cpu_model is not None:
        env["cpu_model"] = cpu_model

    logical_cpus = to_int(sysctl("hw.logicalcpu"))

    physical_cpus = to_int(sysctl("hw.physicalcpu"))
    if physical_cpus is not None:
        env["physical_cpus"] = physical_cpus

    if logical_cpus is not None and physical_cpus is not None:
        env["smt_enabled"] = logical_cpus > physical_cpus

    swap_in_use = _macos_swap(sysctl("vm.swapusage"))
    if swap_in_use is not None:
        env["swap_in_use"] = swap_in_use

    batt = run(["pmset", "-g", "batt"])
    if batt is not None:
        env["on_battery"] = "Battery Power" in batt

    low_power_mode = _macos_low_power(run(["pmset", "-g"]))
    if low_power_mode is not None:
        env["low_power_mode"] = low_power_mode

    return env


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
