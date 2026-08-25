"""Fingerprint: a snapshot of the machine a benchmark ran on."""

from __future__ import annotations

import abc
from collections.abc import Mapping
from typing import Any, cast

type Fingerprint = Mapping[str, Any]
"""Machine facts at run time, as `key -> value`. A missing key means unknown or
not applicable here. Values have to survive a JSON round trip, since a
`Report` carries its fingerprint into the JSON and dir reports."""


def known(**items: Any) -> dict[str, Any]:
    """The facts that could actually be read: a `None` value is dropped rather
    than recorded as an unknown."""
    return {k: v for k, v in items.items() if v is not None}


def display_items(fingerprint: Fingerprint) -> list[tuple[str, str]]:
    """`(key, rendered value)` for each fact, lists comma-joined. The single
    human-readable renderer (CSV preamble, doctor)."""
    out: list[tuple[str, str]] = []
    for key, value in fingerprint.items():
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(x) for x in cast("list[object]", value))
        out.append((key, str(value)))
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


class CompositeProbe(Probe):
    """Merge the snapshots of several probes into one, later probes winning a
    key clash. `None` only when every probe collected nothing."""

    __slots__ = ("probes",)

    def __init__(self, *probes: Probe) -> None:
        super().__init__()
        self.probes = probes

    def collect(self) -> Fingerprint | None:
        collected = [fp for p in self.probes if (fp := p.collect()) is not None]
        if not collected:
            return None
        merged: dict[str, Any] = {}
        for fp in collected:
            merged |= fp
        return merged
