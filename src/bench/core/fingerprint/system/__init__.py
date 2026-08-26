from __future__ import annotations

import platform

from bench.core.fingerprint.base import Fingerprint, Probe
from bench.core.fingerprint.system.common import base
from bench.core.fingerprint.system.linux import collect_linux
from bench.core.fingerprint.system.macos import collect_macos


class SystemProbe(Probe):
    """Probe the host, dispatching on the platform."""

    def __init__(self) -> None:
        super().__init__()

    def collect(self) -> Fingerprint | None:
        system = platform.system()
        if system == "Linux":
            return collect_linux()
        elif system == "Darwin":
            return collect_macos()
        else:
            return base()
