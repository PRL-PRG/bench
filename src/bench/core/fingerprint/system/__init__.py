from __future__ import annotations

import platform

from bench.core.fingerprint.base import Fingerprint, Probe
from bench.core.fingerprint.system.common import (
    SystemEnvironment,
    UnixSystemEnvironment,
    base,
    is_system_environment,
)
from bench.core.fingerprint.system.linux import LinuxSystemEnvironment, collect_linux
from bench.core.fingerprint.system.macos import MacOSSystemEnvironment, collect_macos


class SystemProbe(Probe):
    """Probe the host, dispatching on the platform."""

    def __init__(self) -> None:
        super().__init__()

    def collect(self) -> Fingerprint | None:
        base_env = base()

        system = platform.system()
        if system == "Linux":
            return Fingerprint({**base_env, **collect_linux()})
        elif system == "Darwin":
            return Fingerprint({**base_env, **collect_macos()})
        else:
            return Fingerprint(base_env)


__all__ = [
    "SystemProbe",
    "SystemEnvironment",
    "UnixSystemEnvironment",
    "is_system_environment",
    "LinuxSystemEnvironment",
    "MacOSSystemEnvironment",
]
