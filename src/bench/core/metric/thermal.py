"""CPU temperature and thermal throttling during each execution.

A hot or throttled CPU runs slower, so the measurements taken on it are
suspect. `Thermal` is both a `Hook` and a `Metric`; add the same object as
each::

    t = Thermal()
    bench("x").with_hook(t).with_metric(t)

It emits two process samples, `cpu_temp_max` (C) and `cpu_throttle` (throttle
events), marked `extra={"suspect": True}` when the CPU throttled or came within
`margin` degrees of a limit the hardware declares. On Linux it reads the CPU's
hwmon and thermal-zone sensors and the kernel's throttle counters; where they
are missing it emits nothing, and a sensor that fails never fails a benchmark.
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

# hwmon drivers of CPU packages, and the labels of their package sensors
_HWMON = {"coretemp": ("Package id",), "k10temp": ("Tctl", "Tdie")}
# thermal zones that measure the CPU, by their type
_ZONES = ("cpu", "pkg", "soc")
_TRIPS = ("passive", "hot", "critical")

type Sensor = tuple[Path, int | None]  # its reading, and its limit in m°C


def _text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _int(path: Path) -> int | None:
    try:
        return int(_text(path))
    except ValueError:
        return None


def cpu_sensors(sysfs: Path = Path("/sys")) -> list[Sensor]:
    """The CPU temperature sensors, each with the limit it declares."""
    sensors: list[Sensor] = []
    for hwmon in sorted(sysfs.glob("class/hwmon/hwmon*")):
        labels = _HWMON.get(_text(hwmon / "name"))
        for label in sorted(hwmon.glob("temp*_label")) if labels else []:
            if _text(label).startswith(labels or ()):
                base = str(label).removesuffix("_label")
                sensors.append((Path(f"{base}_input"), _int(Path(f"{base}_crit"))))
    if sensors:
        # where a driver covers the package, thermal zones add only firmware
        # policy zones, whose trip points are not CPU limits
        return sensors
    for zone in sorted(sysfs.glob("class/thermal/thermal_zone*")):
        if any(z in _text(zone / "type").lower() for z in _ZONES):
            trips = [
                _int(Path(str(t).replace("_type", "_temp")))
                for t in zone.glob("trip_point_*_type")
                if _text(t) in _TRIPS
            ]
            limits = [t for t in trips if t is not None and t > 0]
            sensors.append((zone / "temp", min(limits, default=None)))
    return sensors


def throttle_events(sysfs: Path = Path("/sys")) -> int | None:
    """Thermal-throttle events counted so far, summed over all CPUs."""
    files = sysfs.glob("devices/system/cpu/cpu[0-9]*/thermal_throttle/*_throttle_count")
    counts = [c for c in map(_int, files) if c is not None]
    return sum(counts) if counts else None


class _Watch:
    """Samples the sensors every `interval` seconds until stopped."""

    def __init__(self, thermal: Thermal) -> None:
        self.thermal = thermal
        self.hottest: int | None = None
        self.hot = False
        self.throttles_before = throttle_events(thermal.sysfs)
        self.throttles: int | None = None
        self._stop = threading.Event()
        self._sample()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _sample(self) -> None:
        for path, limit in self.thermal.sensors:
            if (t := _int(path)) is None:
                continue
            self.hottest = t if self.hottest is None else max(self.hottest, t)
            if limit is not None and t >= limit - self.thermal.margin * 1000:
                self.hot = True

    def _loop(self) -> None:
        while not self._stop.wait(self.thermal.interval):
            self._sample()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()
        after = throttle_events(self.thermal.sysfs)
        if self.throttles_before is not None and after is not None:
            self.throttles = after - self.throttles_before


class Thermal(Hook, Metric):
    """CPU temperature and thermal throttling around each execution."""

    def __init__(
        self, interval: float = 1.0, margin: float = 10.0, sysfs: Path = Path("/sys")
    ) -> None:
        Metric.__init__(self, "cpu_temp_max", "C")
        self.interval, self.margin, self.sysfs = interval, margin, sysfs
        try:
            self.sensors = cpu_sensors(sysfs)
            self.observable = bool(self.sensors) or throttle_events(sysfs) is not None
        except Exception:
            self.sensors, self.observable = [], False
        self._local = threading.local()  # executions may run in parallel threads

    def setup(self, benchmark: Benchmark) -> None:
        self._local.watch = None
        if self.observable:
            try:
                self._local.watch = _Watch(self)
            except Exception:
                pass

    def teardown(self, benchmark: Benchmark) -> None:
        if (watch := getattr(self._local, "watch", None)) is not None:
            watch.stop()

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        watch: _Watch | None = getattr(self._local, "watch", None)
        self._local.watch = None
        if watch is None:
            return
        suspect = watch.hot or (watch.throttles or 0) > 0
        extra = {"suspect": True} if suspect else {}
        if watch.hottest is not None:
            yield self.get_sample(watch.hottest / 1000, extra=extra)
        if watch.throttles is not None:
            yield self.get_sample(
                float(watch.throttles), "cpu_throttle", "", extra=extra
            )
