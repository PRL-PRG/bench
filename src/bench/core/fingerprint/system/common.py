from __future__ import annotations

import os
import platform
from datetime import datetime

from bench.core.fingerprint.base import Fingerprint


def base() -> Fingerprint:
    """The platform-independent fields, set on every snapshot."""
    try:
        load: list[float] | None = list(os.getloadavg())
    except (OSError, AttributeError):
        load = None
    return Fingerprint(
        timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
        hostname=platform.node(),
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        logical_cpus=os.cpu_count(),
        load_avg=load,
    )
