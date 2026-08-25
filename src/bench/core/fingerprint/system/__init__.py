from __future__ import annotations

import platform

from bench.core.fingerprint.base import Fingerprint, Probe
from bench.core.fingerprint.system.common import base
from bench.core.fingerprint.system.linux import collect_linux
from bench.core.fingerprint.system.macos import collect_macos


class SystemProbe(Probe):
    """Probe the host, dispatching on the platform."""

    __slots__ = ("_cache",)

    def __init__(self) -> None:
        super().__init__()
        self._cache: Fingerprint | None = None

    def collect(self) -> Fingerprint | None:
        if self._cache is None:
            system = platform.system()
            if system == "Linux":
                self._cache = collect_linux()
            elif system == "Darwin":
                self._cache = collect_macos()
            else:
                self._cache = base()

        return self._cache
