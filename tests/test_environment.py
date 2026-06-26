"""Environment collection strategies.

`SystemEnvironment` reads platform facts (failure -> None per field);
`NoEnvironment` is the off switch. Linux probes read a sysfs/proc tree (here a
fake one under tmp_path); macOS probes go through an injected command runner.
"""

from pathlib import Path

from bench.core.environment import (
    Environment,
    NoEnvironment,
    SystemEnvironment,
    collect_linux,
    collect_macos,
)


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


def test_no_environment_collects_nothing():
    assert NoEnvironment().collect() is None


def test_display_items_joins_lists_and_skips_none():
    env = Environment(
        system="Linux", governors=["performance", "powersave"], cpu_model=None
    )
    items = dict(env.display_items())
    assert items["governors"] == "performance, powersave"
    assert items["system"] == "Linux"
    assert "cpu_model" not in items  # None fields are skipped


def test_collect_linux_parses_sysfs(tmp_path: Path):
    _fake_linux_tree(tmp_path)
    env = collect_linux(root=tmp_path)
    assert env.governors == ["performance", "powersave"]
    assert env.turbo_enabled is True
    assert env.aslr == 2
    assert env.swappiness == 60
    assert env.perf_event_paranoid == 2
    assert env.transparent_hugepage == "madvise"
    assert env.smt_enabled is True
    assert env.swap_in_use is True
    assert env.on_battery is False
    assert env.cpu_model == "Test CPU"


def test_collect_linux_turbo_via_boost(tmp_path: Path):
    # No intel_pstate; cpufreq/boost=1 means turbo enabled.
    _write(tmp_path / "sys/devices/system/cpu/cpufreq/boost", "1\n")
    env = collect_linux(root=tmp_path)
    assert env.turbo_enabled is True


def test_collect_linux_missing_files_are_none(tmp_path: Path):
    env = collect_linux(root=tmp_path)
    assert env.governors is None
    assert env.turbo_enabled is None
    assert env.aslr is None
    assert env.swappiness is None
    assert env.perf_event_paranoid is None
    assert env.transparent_hugepage is None
    assert env.on_battery is None
    # Common fields are still present.
    assert env.system != ""
    assert env.python_version != ""


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

    env = collect_macos(run=run)
    assert env.cpu_model == "Apple M2"
    assert env.logical_cpus == 8
    assert env.physical_cpus == 8
    assert env.smt_enabled is False
    assert env.swap_in_use is True
    assert env.on_battery is True
    assert env.low_power_mode is True


def test_collect_macos_missing_values_are_none():
    # sysctl/pmset-only fields go None; logical_cpus keeps its os.cpu_count() base.
    env = collect_macos(run=lambda _cmd: None)
    assert env.cpu_model is None
    assert env.physical_cpus is None
    assert env.on_battery is None
    assert env.low_power_mode is None
    assert env.system != ""


def test_system_environment_smoke():
    env = SystemEnvironment().collect()
    assert env is not None
    assert env.system != ""
    assert env.hostname != ""
