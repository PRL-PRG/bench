from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.core.fingerprint.base import known
from bench.core.fingerprint.system.common import base
from bench.io import read_bracketed, read_int, read_text, to_int

# Filesystem locations of the Linux tuning knobs, shared with `bench.denoise` so
# a path change is a one-file edit. The governor glob and turbo files are under
# `CPU_DIR`; paranoid/swappiness/aslr under `/proc`; THP under the root.
CPU_DIR = "sys/devices/system/cpu"
GOVERNOR_GLOB = "cpu[0-9]*/cpufreq/scaling_governor"
NO_TURBO = "intel_pstate/no_turbo"
CPUFREQ_BOOST = "cpufreq/boost"
THP_ENABLED = "sys/kernel/mm/transparent_hugepage/enabled"
PERF_EVENT_PARANOID = "sys/kernel/perf_event_paranoid"
SWAPPINESS = "sys/vm/swappiness"
ASLR = "sys/kernel/randomize_va_space"


def collect_linux(root: Path = Path("/")) -> dict[str, Any]:
    sys_cpu = root / CPU_DIR
    proc = root / "proc"
    govs = sorted(
        {g for p in sys_cpu.glob(GOVERNOR_GLOB) if (g := read_text(p)) is not None}
    )
    cpu_model, physical = _parse_cpuinfo(read_text(proc / "cpuinfo"))
    return base() | known(
        cpu_model=cpu_model,
        physical_cpus=physical,
        governors=govs or None,
        turbo_enabled=_linux_turbo(sys_cpu),
        aslr=read_int(proc / ASLR),
        transparent_hugepage=read_bracketed(root / THP_ENABLED),
        smt_enabled=_linux_smt(sys_cpu),
        swappiness=read_int(proc / SWAPPINESS),
        swap_in_use=_swap_in_use(proc / "swaps"),
        perf_event_paranoid=read_int(proc / PERF_EVENT_PARANOID),
        on_battery=_on_battery(root / "sys/class/power_supply"),
    )


def _linux_turbo(sys_cpu: Path) -> bool | None:
    no_turbo = read_text(sys_cpu / NO_TURBO)
    if no_turbo is not None:
        return no_turbo == "0"
    boost = read_text(sys_cpu / CPUFREQ_BOOST)
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
