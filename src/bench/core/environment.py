"""EnvironmentCollector: a snapshot of the machine a benchmark ran on."""

from __future__ import annotations

import abc
import dataclasses
import os
import platform
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from bench.utils import read_bracketed, read_int, read_text, to_int

# Runs an external command, returning trimmed stdout or None on any failure.
type EnvRunner = Callable[[list[str]], str | None]

type Severity = Literal["warn", "high"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One actionable finding. `fix` is a command/setting that resolves it."""

    severity: Severity
    message: str
    fix: str | None = None


@dataclass(frozen=True, slots=True)
class Environment:
    """Machine facts at run time. `None` = unknown or not applicable here."""

    timestamp: str = ""
    hostname: str = ""
    system: str = ""
    release: str = ""
    machine: str = ""
    python_version: str = ""
    logical_cpus: int | None = None
    physical_cpus: int | None = None
    cpu_model: str | None = None
    load_avg: list[float] | None = None
    # Linux-only knobs (None on other platforms)
    # TODO: it would be nice to have a better abstraction for "platform-specific" fields
    governors: list[str] | None = None
    turbo_enabled: bool | None = None
    aslr: int | None = None
    transparent_hugepage: str | None = None
    smt_enabled: bool | None = None
    swappiness: int | None = None
    swap_in_use: bool | None = None
    perf_event_paranoid: int | None = None
    on_battery: bool | None = None
    # macOS-only
    low_power_mode: bool | None = None

    def display_items(self) -> list[tuple[str, str]]:
        """`(field, rendered-value)` for each known (non-`None`) field, lists
        comma-joined. The single human-readable renderer (CSV preamble, doctor)."""
        out: list[tuple[str, str]] = []
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if value is None:
                continue
            if isinstance(value, list):
                value = ", ".join(str(x) for x in cast("list[object]", value))
            out.append((f.name, str(value)))
        return out


class EnvironmentCollector(abc.ABC):
    """Strategy: collect a snapshot of the machine, or nothing."""

    __slots__ = ()

    @abc.abstractmethod
    def collect(self) -> Environment | None:
        """Return a snapshot, or `None` to record no environment."""


@dataclass(frozen=True, slots=True)
class NoEnvironment(EnvironmentCollector):
    """Collects nothing - the off switch."""

    def collect(self) -> Environment | None:
        return None


@dataclass(frozen=True, slots=True)
class SystemEnvironment(EnvironmentCollector):
    """Probe the host, dispatching on the platform."""

    def collect(self) -> Environment | None:
        system = platform.system()
        if system == "Linux":
            return collect_linux()
        if system == "Darwin":
            return collect_macos()
        return _base()


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _base() -> Environment:
    """The platform-independent fields, set on every snapshot."""
    try:
        load: list[float] | None = list(os.getloadavg())
    except (OSError, AttributeError):
        load = None
    return Environment(
        timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
        hostname=platform.node(),
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        logical_cpus=os.cpu_count(),
        load_avg=load,
    )


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------


def collect_linux(root: Path = Path("/")) -> Environment:
    sys_cpu = root / "sys/devices/system/cpu"
    proc = root / "proc"
    govs = sorted(
        {
            g
            for p in sys_cpu.glob("cpu[0-9]*/cpufreq/scaling_governor")
            if (g := read_text(p)) is not None
        }
    )
    cpu_model, physical = _parse_cpuinfo(read_text(proc / "cpuinfo"))
    return dataclasses.replace(
        _base(),
        cpu_model=cpu_model,
        physical_cpus=physical,
        governors=govs or None,
        turbo_enabled=_linux_turbo(sys_cpu),
        aslr=read_int(proc / "sys/kernel/randomize_va_space"),
        transparent_hugepage=read_bracketed(
            root / "sys/kernel/mm/transparent_hugepage/enabled"
        ),
        smt_enabled=_linux_smt(sys_cpu),
        swappiness=read_int(proc / "sys/vm/swappiness"),
        swap_in_use=_swap_in_use(proc / "swaps"),
        perf_event_paranoid=read_int(proc / "sys/kernel/perf_event_paranoid"),
        on_battery=_on_battery(root / "sys/class/power_supply"),
    )


def _linux_turbo(sys_cpu: Path) -> bool | None:
    no_turbo = read_text(sys_cpu / "intel_pstate/no_turbo")
    if no_turbo is not None:
        return no_turbo == "0"
    boost = read_text(sys_cpu / "cpufreq/boost")
    if boost is not None:
        return boost == "1"
    return None


def _linux_smt(sys_cpu: Path) -> bool | None:
    ctrl = read_text(sys_cpu / "smt/control")
    if ctrl is None:
        return None
    return ctrl == "on"


def _parse_cpuinfo(text: str | None) -> tuple[str | None, int | None]:
    if not text:
        return None, None
    model: str | None = None
    cores: set[tuple[str, str]] = set()
    pid: str | None = None
    cid: str | None = None
    for line in text.splitlines():
        if not line.strip():
            pid = cid = None
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "model name" and model is None:
            model = value
        elif key == "physical id":
            pid = value
        elif key == "core id":
            cid = value
        if pid is not None and cid is not None:
            cores.add((pid, cid))
    return model, (len(cores) or None)


def _swap_in_use(swaps: Path) -> bool | None:
    text = read_text(swaps)
    if text is None:
        return None
    used = 0
    for line in text.splitlines()[1:]:  # skip the header row
        parts = line.split()
        if len(parts) >= 4 and (n := to_int(parts[3])) is not None:
            used += n
    return used > 0


def _on_battery(power_supply: Path) -> bool | None:
    if not power_supply.exists():
        return None
    for name in ("AC", "ACAD", "ADP0", "ADP1"):
        online = read_text(power_supply / name / "online")
        if online is not None:
            return online == "0"
    for bat in power_supply.glob("BAT*"):
        status = read_text(bat / "status")
        if status is not None:
            return status == "Discharging"
    return None


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


def _sysctl_run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def collect_macos(run: EnvRunner = _sysctl_run) -> Environment:
    def sysctl(key: str) -> str | None:
        return run(["sysctl", "-n", key])

    physical = to_int(sysctl("hw.physicalcpu"))
    logical = to_int(sysctl("hw.logicalcpu"))
    smt = logical > physical if logical is not None and physical is not None else None
    batt = run(["pmset", "-g", "batt"])
    base = _base()
    return dataclasses.replace(
        base,
        cpu_model=sysctl("machdep.cpu.brand_string"),
        logical_cpus=logical if logical is not None else base.logical_cpus,
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
