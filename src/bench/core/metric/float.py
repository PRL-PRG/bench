from __future__ import annotations

from collections.abc import Iterable

from bench.core.metric.base import BuildableMetric, IterationMetric, MetricSource
from bench.model.results import Direction, Sample


class FloatPerLine(IterationMetric, BuildableMetric):
    """Parse non-empty lines of the iteration text as floats, one sample each.

    `line` selects a single 1-based non-empty line (negative counts from the
    end). `None` (the default) parses every non-empty line. A `line` index out
    of range emits nothing.
    """

    unit: str
    line: int | None

    def __init__(
        self,
        source: MetricSource,
        metric: str,
        line: int | None = None,
        unit: str = "",
        direction: Direction = "uncomparable",
        iterate: bool = True,
    ) -> None:
        super().__init__(source, metric, unit, direction)
        self.unit = unit
        self.line = line
        self.iterate = iterate

    def process_text(self, text: str) -> Iterable[Sample]:
        idx = 0

        if not text:
            return
        lines = [s for s in (ln.strip() for ln in text.split("\n")) if s]
        if self.line is not None:
            select_idx = self.line - 1 if self.line > 0 else self.line
            try:
                lines = [lines[select_idx]]
            except IndexError:
                return
        for line in lines:
            try:
                yield self.get_sample(
                    value=float(line), iteration=idx if self.iterate else None
                )
                idx += 1
            except ValueError:
                continue

    @staticmethod
    def last_line(
        source: MetricSource,
        metric: str,
        unit: str = "",
        direction: Direction = "uncomparable",
    ) -> FloatPerLine:
        """Parse only the last non-empty line."""
        return FloatPerLine(
            source=source, metric=metric, line=-1, unit=unit, direction=direction
        )
