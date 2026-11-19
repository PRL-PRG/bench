"""Thermal: CPU temperature and throttling around each execution, read from a
fake sysfs tree (Linux hwmon and thermal_throttle counters)."""

from pathlib import Path

from bench import Controller, FixedRuns, Reporter, Thermal, bench, suite
from bench.builder.suite import plan
from bench.model.invocation import InvocationResult
from bench.params import Params


def _sysfs(
    root: Path, temp_mc: int = 50000, throttles: int = 0, crit_mc: int | None = 100000
) -> Path:
    hwmon = root / "class/hwmon/hwmon2"
    hwmon.mkdir(parents=True, exist_ok=True)
    (hwmon / "name").write_text("coretemp\n")
    (hwmon / "temp1_label").write_text("Package id 0\n")
    (hwmon / "temp1_input").write_text(f"{temp_mc}\n")
    if crit_mc is not None:
        (hwmon / "temp1_crit").write_text(f"{crit_mc}\n")
    (hwmon / "temp2_label").write_text("Core 0\n")
    (hwmon / "temp2_input").write_text("99000\n")  # a core, not the package: ignored
    tt = root / "devices/system/cpu/cpu0/thermal_throttle"
    tt.mkdir(parents=True, exist_ok=True)
    (tt / "core_throttle_count").write_text(f"{throttles}\n")
    (tt / "package_throttle_count").write_text("0\n")
    return root


def _measure(t: Thermal, during) -> dict[str, tuple[float, dict]]:
    t.setup(None)  # type: ignore[arg-type]
    during()
    t.teardown(None)  # type: ignore[arg-type]
    samples = t.process(InvocationResult.__new__(InvocationResult))
    return {s.metric: (s.value, dict(s.extra)) for s in samples}


def test_cool_and_unthrottled_is_not_suspect(tmp_path: Path):
    t = Thermal(sysfs=_sysfs(tmp_path))
    assert _measure(t, lambda: None) == {
        "cpu_temp_max": (50.0, {}),
        "cpu_throttle": (0.0, {}),
    }


def test_hottest_reading_is_kept_and_flagged_near_crit(tmp_path: Path):
    root = _sysfs(tmp_path)
    t = Thermal(sysfs=root)

    def heat_up():
        _sysfs(root, temp_mc=93000)

    got = _measure(t, heat_up)
    assert got["cpu_temp_max"] == (93.0, {"suspect": True})  # within 10 C of crit 100


def test_throttling_during_the_execution_is_flagged(tmp_path: Path):
    root = _sysfs(tmp_path, throttles=4)
    t = Thermal(sysfs=root)
    got = _measure(t, lambda: _sysfs(root, throttles=6))
    assert got["cpu_throttle"] == (2.0, {"suspect": True})
    assert got["cpu_temp_max"] == (50.0, {"suspect": True})


def test_without_crit_the_fallback_applies(tmp_path: Path):
    root = _sysfs(tmp_path, temp_mc=85000, crit_mc=None)
    assert _measure(Thermal(sysfs=root), lambda: None)["cpu_temp_max"] == (85.0, {})
    assert _measure(Thermal(sysfs=root, margin=20), lambda: None)["cpu_temp_max"] == (
        85.0,
        {"suspect": True},
    )


def test_no_sensors_no_samples(tmp_path: Path):
    assert _measure(Thermal(sysfs=tmp_path), lambda: None) == {}


def test_as_hook_and_metric_of_a_benchmark(tmp_path: Path):
    t = Thermal(sysfs=_sysfs(tmp_path, temp_mc=61000))
    b = bench("b").with_command(["true"]).with_hook(t).with_metric(t)
    s = suite("S").add(b).with_cwd(Path("/tmp")).with_runs(FixedRuns(2))

    executions = Controller().run_benchmark(plan([s], Params())[0], Reporter(), False)

    for e in executions:
        assert {x.metric: x.value for x in e.process_samples} == {
            "cpu_temp_max": 61.0,
            "cpu_throttle": 0.0,
        }
