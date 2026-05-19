"""Sample and Report: immutable measurement records.

A ``Sample`` is one parsed metric from one ``ScheduledExecution``. A ``Report``
is the in-memory accumulation of all Samples from one run. Both are pure
data — no references to live Execution/Processor objects — so they round-trip
through JSON without surprises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from benchr.grammar.execution import Phase


@dataclass(frozen=True, slots=True)
class Sample:
    """One parsed measurement.

    ``info`` is a canonical (sorted) tuple of (key, value) pairs that identify
    a benchmark variant — e.g. ``(("compiler", "gcc"), ("opt", "O2"))`` for a
    matrix cell. The tuple form is hashable so Samples group cleanly.

    ``phase`` is ``"warmup"`` or ``"measure"``. Stats default to excluding
    warmup; raw outputs (JSON, CSV, dir) always include both.

    ``lower_is_better`` is ``None`` for metrics that aren't comparable
    (e.g. ``failed`` flags); ``True``/``False`` is set by the Processor.
    """

    suite: str
    benchmark: str
    info: tuple[tuple[str, str], ...]
    run: int
    phase: Phase
    metric: str
    value: float
    unit: str = ""
    lower_is_better: bool | None = None


def info_keys(samples: Iterable[Sample]) -> list[str]:
    """Stable list of info-column names across a stream of samples."""
    seen: dict[str, None] = {}
    for s in samples:
        for k, _ in s.info:
            seen.setdefault(k, None)
    return list(seen)


@dataclass(slots=True)
class Report:
    """The accumulating list of Samples plus optional metadata."""

    samples: list[Sample] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def metrics(self) -> list[str]:
        return list({s.metric: None for s in self.samples})

    def info_keys(self) -> list[str]:
        return info_keys(self.samples)

    def extend(self, samples: Iterable[Sample]) -> None:
        self.samples.extend(samples)


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def report_to_json(report: Report, *, indent: int = 2) -> str:
    return json.dumps(_to_dict(report), indent=indent)


def report_from_json(text: str) -> Report:
    return _from_dict(json.loads(text))


def _to_dict(report: Report) -> dict[str, Any]:
    return {
        "metadata": report.metadata,
        "samples": [
            {
                "suite": s.suite,
                "benchmark": s.benchmark,
                "info": list(s.info),
                "run": s.run,
                "phase": s.phase,
                "metric": s.metric,
                "value": s.value,
                **({"unit": s.unit} if s.unit else {}),
                **(
                    {"lower_is_better": s.lower_is_better}
                    if s.lower_is_better is not None
                    else {}
                ),
            }
            for s in report.samples
        ],
    }


def _from_dict(d: dict[str, Any]) -> Report:
    samples: list[Sample] = []
    for sd in d.get("samples", []):
        info = tuple((k, v) for k, v in sd.get("info", []))
        samples.append(
            Sample(
                suite=sd["suite"],
                benchmark=sd["benchmark"],
                info=info,
                run=sd["run"],
                phase=sd.get("phase", "measure"),
                metric=sd["metric"],
                value=sd["value"],
                unit=sd.get("unit", ""),
                lower_is_better=sd.get("lower_is_better"),
            )
        )
    return Report(samples=samples, metadata=d.get("metadata", {}))
