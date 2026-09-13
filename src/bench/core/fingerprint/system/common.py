# IMPORTANT: Do not add `from __future__ import annotations` as it breaks SystemEnvironment

import contextlib
import os
import platform
from collections.abc import Mapping
from datetime import datetime
from typing import Any, NotRequired, TypedDict


class SystemEnvironment(TypedDict):
    """Machine facts at run time. `None` = unknown or not applicable here."""

    timestamp: str
    hostname: str
    system: str
    release: str
    machine: str
    python_version: str

    logical_cpus: NotRequired[int]
    load_avg: NotRequired[list[float]]

    niceness: NotRequired[int]


class UnixSystemEnvironment(TypedDict, total=False):
    cpu_model: str
    physical_cpus: int
    smt_enabled: bool
    swap_in_use: bool
    on_battery: bool


def is_system_environment(inst: Mapping[str, Any]) -> bool:
    for name in SystemEnvironment.__required_keys__:
        if name not in inst:
            return False

    return True


def base() -> SystemEnvironment:
    """The platform-independent facts, probed on every machine."""
    env = SystemEnvironment(
        timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
        hostname=platform.node(),
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
        python_version=platform.python_version(),
    )

    with contextlib.suppress(OSError, AttributeError):
        env["load_avg"] = list(os.getloadavg())

    if hasattr(os, "getpriority"):
        env["niceness"] = os.getpriority(os.PRIO_PROCESS, 0)

    logical_cpus = os.cpu_count()
    if logical_cpus is not None:
        env["logical_cpus"] = logical_cpus

    return env
