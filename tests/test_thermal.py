"""Thermal: CPU temperature and throttling around each execution, read from a
fake sysfs tree."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from bench import Controller, FixedRuns, Reporter, Thermal, bench, suite
from bench.builder.suite import plan
from bench.model.invocation import InvocationResult
from bench.params import Params


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    return root


def _coretemp(root: Path, temp: int = 50000, throttles: int = 0) -> Path:
    return _write(
        root,
        {
            "class/hwmon/hwmon2/name": "coretemp\n",
            "class/hwmon/hwmon2/temp1_label": "Package id 0\n",
            "class/hwmon/hwmon2/temp1_input": f"{temp}\n",
            "class/hwmon/hwmon2/temp1_crit": "100000\n",
            "class/hwmon/hwmon2/temp2_label": "Core 0\n",  # not the package
            "class/hwmon/hwmon2/temp2_input": "99000\n",
            "devices/system/cpu/cpu0/thermal_throttle/core_throttle_count": f"{throttles}\n",
            "devices/system/cpu/cpu0/thermal_throttle/package_throttle_count": "0\n",
        },
    )


def _measure(t: Thermal, during: Callable[[], Any] = lambda: None) -> dict[str, Any]:
    t.setup(None)  # type: ignore[arg-type]
    during()
    t.teardown(None)  # type: ignore[arg-type]
    samples = t.process(InvocationResult.__new__(InvocationResult))
    return {s.metric: (s.value, dict(s.extra)) for s in samples}


def test_cool_and_unthrottled(tmp_path: Path):
    t = Thermal(sysfs=_coretemp(tmp_path))
    assert _measure(t) == {"cpu_temp_max": (50.0, {}), "cpu_throttle": (0.0, {})}


def test_near_the_declared_limit_is_suspect(tmp_path: Path):
    root = _coretemp(tmp_path)
    got = _measure(Thermal(sysfs=root), lambda: _coretemp(root, temp=93000))
    assert got["cpu_temp_max"] == (93.0, {"suspect": True})  # within 10 C of crit


def test_throttling_is_suspect(tmp_path: Path):
    root = _coretemp(tmp_path, throttles=4)
    got = _measure(Thermal(sysfs=root), lambda: _coretemp(root, throttles=6))
    assert got["cpu_throttle"] == (2.0, {"suspect": True})


def test_thermal_zone_uses_its_lowest_trip_point(tmp_path: Path):
    root = _write(
        tmp_path,
        {
            "class/thermal/thermal_zone0/type": "cpu-thermal\n",
            "class/thermal/thermal_zone0/temp": "77000\n",
            "class/thermal/thermal_zone0/trip_point_0_type": "active\n",  # a fan, not a limit
            "class/thermal/thermal_zone0/trip_point_0_temp": "60000\n",
            "class/thermal/thermal_zone0/trip_point_1_type": "passive\n",
            "class/thermal/thermal_zone0/trip_point_1_temp": "85000\n",
            "class/thermal/thermal_zone0/trip_point_2_type": "critical\n",
            "class/thermal/thermal_zone0/trip_point_2_temp": "105000\n",
            "class/thermal/thermal_zone1/type": "battery\n",  # not the CPU
            "class/thermal/thermal_zone1/temp": "99000\n",
        },
    )
    assert _measure(Thermal(sysfs=root)) == {"cpu_temp_max": (77.0, {"suspect": True})}


def test_thermal_zones_are_ignored_where_hwmon_covers_the_cpu(tmp_path: Path):
    root = _write(
        _coretemp(tmp_path),
        {
            "class/thermal/thermal_zone0/type": "CPUV\n",  # a firmware policy zone
            "class/thermal/thermal_zone0/temp": "55000\n",
            "class/thermal/thermal_zone0/trip_point_0_type": "passive\n",
            "class/thermal/thermal_zone0/trip_point_0_temp": "60050\n",
        },
    )
    assert _measure(Thermal(sysfs=root))["cpu_temp_max"] == (50.0, {})


def test_without_a_declared_limit_nothing_is_guessed(tmp_path: Path):
    root = _write(
        tmp_path,
        {
            "class/hwmon/hwmon0/name": "k10temp\n",
            "class/hwmon/hwmon0/temp1_label": "Tctl\n",
            "class/hwmon/hwmon0/temp1_input": "99000\n",
        },
    )
    assert _measure(Thermal(sysfs=root)) == {"cpu_temp_max": (99.0, {})}


def test_no_sensors_no_samples(tmp_path: Path):
    t = Thermal(sysfs=tmp_path)
    assert not t.observable
    assert _measure(t) == {}


def test_broken_sensors_never_raise(tmp_path: Path):
    root = _write(
        tmp_path,
        {
            "class/hwmon/hwmon0/name": "coretemp\n",
            "class/hwmon/hwmon0/temp1_label": "Package id 0\n",
            "class/hwmon/hwmon0/temp1_input": "garbage\n",
            "class/hwmon/hwmon0/temp1_crit": "\xff\n",
            "class/thermal/thermal_zone0/type": "x86_pkg_temp\n",
            "devices/system/cpu/cpu0/thermal_throttle/core_throttle_count": "n/a\n",
        },
    )
    (root / "class/thermal/thermal_zone0/temp").mkdir()  # unreadable as a file
    assert _measure(Thermal(sysfs=root)) == {}


def test_as_hook_and_metric_of_a_benchmark(tmp_path: Path):
    t = Thermal(sysfs=_coretemp(tmp_path, temp=61000))
    b = bench("b").with_command(["true"]).with_hook(t).with_metric(t)
    s = suite("S").add(b).with_cwd(Path("/tmp")).with_runs(FixedRuns(2))

    executions = Controller().run_benchmark(plan([s], Params())[0], Reporter(), False)

    for e in executions:
        assert {x.metric: x.value for x in e.process_samples} == {
            "cpu_temp_max": 61.0,
            "cpu_throttle": 0.0,
        }
