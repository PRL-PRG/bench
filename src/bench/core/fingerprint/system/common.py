from __future__ import annotations

import os
import platform
from datetime import datetime
from typing import Any

from bench.core.fingerprint.base import known


def base() -> dict[str, Any]:
    """The platform-independent facts, probed on every machine."""
    try:
        load: list[float] | None = list(os.getloadavg())
    except (OSError, AttributeError):
        load = None
    return known(
        timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
        hostname=platform.node(),
        system=platform.system(),
        release=platform.release(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        logical_cpus=os.cpu_count(),
        load_avg=load,
    )
