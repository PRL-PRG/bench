"""Sample and Report: immutable measurement records.

A ``Sample`` is one parsed metric from one ``ScheduledExecution``. A
``FailureRecord`` is one *failed* run — identity plus exit code, carrying no
metric. A ``Report`` accumulates both. All are pure data — no references to
live Execution/Processor objects — so they round-trip through JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from benchr.grammar.execution import (
    FailedExecutionResult,
    Phase,
    ExecutionResult,
    ScheduledExecution,
)


@dataclass(frozen=True, slots=True)
class Sample:
    """One parsed measurement.

    ``info`` is a canonical (sorted) tuple of (key, value) pairs that identify
    a benchmark variant — e.g. ``(("compiler", "gcc"), ("opt", "O2"))`` for a
    matrix cell. The tuple form is hashable so Samples group cleanly.

    ``phase`` is ``"warmup"`` or ``"measure"``. Stats default to excluding
    warmup; raw outputs (JSON, CSV, dir) always include both.

    ``lower_is_better`` is ``None`` for metrics that aren't comparable;
    ``True``/``False`` is set by the Processor.
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


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """One failed run: identity + exit code + a short diagnostic.

    Failures are not Samples — they carry no metric value. Recording them
    structurally lets summaries, JSON and stats report *which* runs failed and
    *why* without a magic ``failed`` metric polluting real measurements.

    ``returncode`` follows the FailedExecutionResult convention: ``124`` = timeout,
    ``-1`` = pre-execution failure (``reason`` set), any other ``> 0`` = exit code.
    ``message`` is the last non-empty line of stderr/stdout.
    """

    suite: str
    benchmark: str
    info: tuple[tuple[str, str], ...]
    run: int
    phase: Phase
    returncode: int
    reason: str | None = None
    message: str = ""

    def identifier(self) -> str:
        out = f"{self.suite}/{self.benchmark}"
        if self.info:
            out += " (" + ", ".join(f"{k}={v}" for k, v in self.info) + ")"
        out += f" #{self.run} [{self.phase}]"
        return out

    @staticmethod
    def from_result(
        sched: ScheduledExecution, pr: FailedExecutionResult
    ) -> "FailureRecord":
        return FailureRecord(
            suite=sched.suite,
            benchmark=sched.benchmark,
            info=sched.info,
            run=sched.run,
            phase=sched.phase,
            returncode=pr.returncode,
            reason=pr.reason,
            message=_diagnostic_excerpt(pr),
        )


def _diagnostic_excerpt(pr: FailedExecutionResult, *, max_len: int = 80) -> str:
    """Last non-empty line of stderr (else stdout); ``"(no output)"`` otherwise."""
    for text in (pr.stderr, pr.stdout):
        if not text:
            continue
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped:
                return stripped[:max_len] + ("…" if len(stripped) > max_len else "")
    return "(no output)"


@dataclass(slots=True)
class Report:
    """The accumulating Samples and FailureRecords plus optional metadata."""

    samples: list[Sample] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    failures: list[FailureRecord] = field(default_factory=list)

    def metrics(self) -> list[str]:
        return list({s.metric: None for s in self.samples})

    def info_keys(self) -> list[str]:
        return info_keys(self.samples)

    def extend(self, samples: Iterable[Sample]) -> None:
        self.samples.extend(samples)

    def add_failure(self, failure: FailureRecord) -> None:
        self.failures.append(failure)

    def record(
        self,
        sched: ScheduledExecution,
        pr: ExecutionResult,
        samples: Iterable[Sample],
    ) -> None:
        """Ingest one execution: keep its samples, log a FailureRecord if it failed."""
        self.samples.extend(samples)
        if isinstance(pr, FailedExecutionResult):
            self.failures.append(FailureRecord.from_result(sched, pr))


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
        "failures": [
            {
                "suite": f.suite,
                "benchmark": f.benchmark,
                "info": list(f.info),
                "run": f.run,
                "phase": f.phase,
                "returncode": f.returncode,
                **({"reason": f.reason} if f.reason else {}),
                **({"message": f.message} if f.message else {}),
            }
            for f in report.failures
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
    failures: list[FailureRecord] = []
    for fd in d.get("failures", []):
        info = tuple((k, v) for k, v in fd.get("info", []))
        failures.append(
            FailureRecord(
                suite=fd["suite"],
                benchmark=fd["benchmark"],
                info=info,
                run=fd["run"],
                phase=fd.get("phase", "measure"),
                returncode=fd["returncode"],
                reason=fd.get("reason"),
                message=fd.get("message", ""),
            )
        )
    return Report(
        samples=samples, metadata=d.get("metadata", {}), failures=failures
    )
