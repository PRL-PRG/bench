from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from bench.core.metric.base import BuildableMetric, IterationMetric, MetricSource
from bench.model.results import Direction, Sample


class RegexMetric(IterationMetric, BuildableMetric):
    """Extract metric values via a regex against the iteration text."""

    def __init__(
        self,
        metric: str,
        regex: re.Pattern[str] | str,
        source: MetricSource,
        *,
        unit: str = "",
        iterate: bool = False,
        direction: Direction = "uncomparable",
        match_group: str | int = 1,
        transform: Callable[[str], float] = float,
        unit_group: str | int | None = None,
    ):
        super().__init__(source, metric, unit, direction)

        if isinstance(regex, str):
            self.regex = re.compile(regex, re.MULTILINE)
        else:
            self.regex = regex

        self.iterate = iterate
        self.match_group = match_group
        self.transform = transform
        self.unit_group = unit_group

    def process_text(self, text: str) -> Iterable[Sample]:
        pattern = self.regex
        idx = 0

        for m in pattern.finditer(text):
            value = self.transform(m.group(self.match_group))
            unit = (
                m.group(self.unit_group) if self.unit_group is not None else self.unit
            )
            yield self.get_sample(
                value=value,
                unit=unit,
                iteration=idx if self.iterate else None,
            )
            idx += 1
