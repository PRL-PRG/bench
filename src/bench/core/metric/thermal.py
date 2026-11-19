"""Thermal: was the CPU hot or throttled while a benchmark ran?

A hot or throttled CPU runs slower, so its numbers are suspect. `Thermal` is
both a `Hook` and a `Metric`; add the same object as each:

    t = Thermal()
    bench("x").with_hook(t).with_metric(t)

Around every execution it samples the CPU package temperature (Linux hwmon:
`coretemp` on Intel, `k10temp` on AMD) and reads the kernel's thermal-throttle
counters, then emits two process samples:

- `cpu_temp_max`: the hottest package temperature seen (C);
- `cpu_throttle`: how many throttle events the CPUs counted (summed over CPUs).

Both carry `extra={"suspect": True}` when the CPU throttled or came within
`margin` degrees of the temperature where it starts throttling (hwmon's `crit`,
else `fallback_crit`). Where the sensors are missing, it emits nothing.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from bench.core.hook import Hook
from bench.core.metric.base import Metric
from bench.model.invocation import InvocationResult
from bench.model.results import Sample

if TYPE_CHECKING:
    from bench.model.benchmark import Benchmark

_DRIVERS = {"coretemp": ("Package id",), "k10temp": ("Tctl", "Tdie")}


def _read(path: Path) -> int | None:
    try:
        return int(path.read_text())
    except (OSError, ValueError):
        return None


def package_sensors(sysfs: Path = Path("/sys")) -> list[tuple[Path, int | None]]:
    """(temp*_input, crit in m°C or None) of every CPU package sensor."""
    sensors: list[tuple[Path, int | None]] = []
    for hwmon in sorted((sysfs / "class/hwmon").glob("hwmon*")):
        try:
            labels = _DRIVERS.get((hwmon / "name").read_text().strip())
        except OSError:
            continue
        if labels is None:
            continue
        for label in sorted(hwmon.glob("temp*_label")):
            if label.read_text().strip().startswith(labels):
                base = label.name.removesuffix("_label")
                sensors.append((hwmon / f"{base}_input", _read(hwmon / f"{base}_crit")))
    return sensors


def throttle_events(sysfs: Path = Path("/sys")) -> int | None:
    """Thermal-throttle events counted so far, summed over all CPUs."""
    counts = [
        _read(f)
        for f in (sysfs / "devices/system/cpu").glob(
            "cpu[0-9]*/thermal_throttle/*_throttle_count"
        )
    ]
    counts = [c for c in counts if c is not None]
    return sum(counts) if counts else None


class _Watch:
    """Sample the hottest package temperature every `interval` s until stopped."""

    def __init__(
        self, sensors: list[tuple[Path, int | None]], sysfs: Path, interval: float
    ) -> None:
        self.sensors, self.sysfs = sensors, sysfs
        self.hottest: int | None = None
        self.throttles_before = throttle_events(sysfs)
        self.throttles: int | None = None
        self._stop = threading.Event()
        self._sample()
        self._thread = threading.Thread(
            target=self._loop, args=(interval,), daemon=True
        )
        self._thread.start()

    def _sample(self) -> None:
        for path, _ in self.sensors:
            t = _read(path)
            if t is not None and (self.hottest is None or t > self.hottest):
                self.hottest = t

    def _loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self._sample()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()
        after = throttle_events(self.sysfs)
        if self.throttles_before is not None and after is not None:
            self.throttles = after - self.throttles_before


class Thermal(Hook, Metric):
    """CPU temperature and throttling during each execution (see module doc)."""

    def __init__(
        self,
        interval: float = 1.0,
        margin: float = 10.0,
        fallback_crit: float = 100.0,
        sysfs: Path = Path("/sys"),
    ) -> None:
        Metric.__init__(self, "cpu_temp_max", "C")
        self.interval, self.margin, self.fallback_crit = interval, margin, fallback_crit
        self.sysfs = sysfs
        self._sensors = package_sensors(sysfs)
        # one watch per thread: executions may run in parallel
        self._local = threading.local()

    def setup(self, benchmark: Benchmark) -> None:
        self._local.watch = _Watch(self._sensors, self.sysfs, self.interval)

    def teardown(self, benchmark: Benchmark) -> None:
        self._local.watch.stop()

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        watch: _Watch | None = getattr(self._local, "watch", None)
        self._local.watch = None
        if watch is None:
            return
        crits = [c / 1000 for _, c in self._sensors if c is not None]
        limit = (min(crits) if crits else self.fallback_crit) - self.margin
        hot = watch.hottest is not None and watch.hottest / 1000 >= limit
        extra = {"suspect": True} if hot or (watch.throttles or 0) > 0 else {}
        if watch.hottest is not None:
            yield self.get_sample(watch.hottest / 1000, extra=extra)
        if watch.throttles is not None:
            yield self.get_sample(
                float(watch.throttles), "cpu_throttle", "", extra=extra
            )
