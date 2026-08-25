from __future__ import annotations

import re
from collections.abc import Iterable

from bench.core.metric.base import IterationMetric, MetricSource
from bench.model.results import Sample


class RebenchMetric(IterationMetric):
    """ReBench log format adapter.

    `optional_prefix: name optional_criterion: iterations=N runtime: V[ms|us]`
    or `optional_prefix: name: criterion: V<unit>`
    Runtime emitted in ms. Non-"total" runtime criteria are ignored.
    """

    _re_runtime = re.compile(
        r"^(?:.*: )?([^\s]+)( [\w\.]+)?: iterations=([0-9]+) "
        r"runtime: (?P<runtime>(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?)"
        r"(?P<unit>[mu])s"
    )
    _re_criterion = re.compile(
        r"^(?:.*: )?([^\s]+): (?P<criterion>[^:]{1,30}):\s*"
        r"(?P<value>(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?)"
        r"(?P<unit>[a-zA-Z]+)"
    )

    iteration: int

    def __init__(
        self,
        source: MetricSource,
    ) -> None:
        super().__init__(source, "runtime", "ms", "lower better")
        self.iteration = 0

    def process_text(self, text: str) -> Iterable[Sample]:
        iteration = 0

        for line in text.split("\n"):
            m = self._re_runtime.match(line)
            if m is not None:
                criterion = m.group(2)
                if criterion is not None and criterion.strip() != "total":
                    continue

                value = float(m.group("runtime"))
                if m.group("unit") == "u":
                    value /= 1000.0

                yield self.get_sample(value=value, iteration=iteration)
                iteration += 1
                continue

            m = self._re_criterion.match(line)
            if m is not None:
                value = float(m.group("value"))
                unit = m.group("unit")
                criterion = m.group("criterion")

                yield self.get_sample(
                    metric=criterion,
                    value=value,
                    iteration=iteration,
                    unit=unit,
                )

                if criterion == "total":
                    iteration += 1
