from __future__ import annotations

from pathlib import Path

from bench.core.fingerprint.system.common import UnixSystemEnvironment
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


class LinuxSystemEnvironment(UnixSystemEnvironment, total=False):
    governors: list[str]
    turbo_enabled: bool
    aslr: int
    transparent_hugepage: str
    swappiness: int
    perf_event_paranoid: int


def collect_linux(root: Path = Path("/")) -> LinuxSystemEnvironment:
    sys_cpu = root / CPU_DIR
    proc = root / "proc"

    env = LinuxSystemEnvironment()

    cpu_model, physical_cpus = _parse_cpuinfo(read_text(proc / "cpuinfo"))
    if cpu_model is not None:
        env["cpu_model"] = cpu_model
    if physical_cpus is not None:
        env["physical_cpus"] = physical_cpus

    governors = _linux_goveners(sys_cpu)
    if governors:
        env["governors"] = governors

    turbo_enabled = _linux_turbo(sys_cpu)
    if turbo_enabled is not None:
        env["turbo_enabled"] = turbo_enabled

    aslr = read_int(proc / ASLR)
    if aslr is not None:
        env["aslr"] = aslr

    transparent_hugepage = read_bracketed(root / THP_ENABLED)
    if transparent_hugepage is not None:
        env["transparent_hugepage"] = transparent_hugepage

    smt_enabled = _linux_smt(sys_cpu)
    if smt_enabled is not None:
        env["smt_enabled"] = smt_enabled

    swappiness = read_int(proc / SWAPPINESS)
    if swappiness is not None:
        env["swappiness"] = swappiness

    swap_in_use = _swap_in_use(proc / "swaps")
    if swap_in_use is not None:
        env["swap_in_use"] = swap_in_use

    perf_event_paranoid = read_int(proc / PERF_EVENT_PARANOID)
    if perf_event_paranoid is not None:
        env["perf_event_paranoid"] = perf_event_paranoid

    on_battery = _on_battery(root / "sys/class/power_supply")
    if on_battery is not None:
        env["on_battery"] = on_battery

    return env


def _linux_goveners(sys_cpu: Path):
    def it():
        for p in sys_cpu.glob(GOVERNOR_GLOB):
            t = read_text(p)
            if t is not None:
                yield t

    return sorted({*it()})


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
