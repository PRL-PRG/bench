"""Fingerprint: a snapshot of the machine a benchmark ran on."""

from __future__ import annotations

import abc
import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Fingerprint(Mapping[str, Any]):
    """Machine facts at run time, as `key -> value`. A missing key means unknown or
    not applicable here."""

    data: Mapping[str, Any] = dataclasses.field(default_factory=dict[str, Any])

    def __or__(self, other: Fingerprint) -> Fingerprint:
        return Fingerprint({**self.data, **other.data})

    @staticmethod
    def from_optional(**kwargs: Any | None) -> Fingerprint:
        """The facts that could actually be read: a `None` value is dropped rather
        than recorded as an unknown."""
        return Fingerprint({k: v for k, v in kwargs.items() if v is not None})

    def __getitem__(self, key: str, /):
        return self.data.__getitem__(key)

    def __iter__(self):
        return self.data.__iter__()

    def __len__(self) -> int:
        return self.data.__len__()


class Probe(abc.ABC):
    """Strategy: collect a snapshot of the machine, or nothing."""

    __slots__ = ()

    @abc.abstractmethod
    def collect(self) -> Fingerprint | None:
        """Return a snapshot, or `None` to record no fingerprint."""

    def __and__(self, other: Probe) -> Probe:
        return CompositeProbe(self, other)


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
        res = None

        for p in self.probes:
            c = p.collect()

            if res is None:
                res = c
            elif c is not None:
                res |= c

        return res
