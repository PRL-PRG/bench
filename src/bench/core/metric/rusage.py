from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Literal

from bench.core.metric.base import (
    BuildableMetric,
    Metric,
)
from bench.model.invocation import InvocationResult
from bench.model.results import Direction, Sample


class RUsage(BuildableMetric):
    """Emit one sample from a single `resource.struct_rusage` field."""

    Field = Literal[
        "ru_utime",
        "ru_stime",
        "ru_maxrss",
        "ru_ixrss",
        "ru_idrss",
        "ru_isrss",
        "ru_minflt",
        "ru_majflt",
        "ru_nswap",
        "ru_inblock",
        "ru_oublock",
        "ru_msgsnd",
        "ru_msgrcv",
        "ru_nsignals",
        "ru_nvcsw",
        "ru_nivcsw",
    ]

    def __init__(
        self,
        field: Field,
        metric: str,
        unit: str = "",
        direction: Direction = "uncomparable",
    ) -> None:
        super().__init__(metric, unit, direction)
        self.field = field

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        if data.rusage is None:
            return
        value = float(getattr(data.rusage, self.field))
        # macOS reports ru_maxrss in bytes, not kB.
        if sys.platform == "darwin" and self.field == "ru_maxrss":
            value /= 1024.0
        yield self.get_sample(value=value, unit=self.unit)


def max_rss() -> Metric:
    """RSS in kB, lower-is-better. macOS byte-vs-kB normalization handled."""
    return RUsage("ru_maxrss", "max_rss", "kB").lower_is_better()
