"""Fingerprint: a snapshot of the machine a benchmark ran on."""

from __future__ import annotations

import abc
import dataclasses
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class Fingerprint:
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


class Probe(abc.ABC):
    """Strategy: collect a snapshot of the machine, or nothing."""

    __slots__ = ()

    @abc.abstractmethod
    def collect(self) -> Fingerprint | None:
        """Return a snapshot, or `None` to record no fingerprint."""


class NoProbe(Probe):
    """Collects nothing - the off switch."""

    __slots__ = ()

    def collect(self) -> Fingerprint | None:
        return None
