"""Sample and Report: immutable measurement records.

A ``Sample`` is one parsed metric from one ``ScheduledExecution``. A
``RunRecord`` is the context of one *execution* — identity, command, outcome —
carrying no metric value; a failed run is a RunRecord whose ``failure`` is set.
A ``Report`` accumulates both. All are pure data — no references to live
Execution/Processor objects — so they round-trip through JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from benchr.grammar.execution import (
    Phase,
    ExecutionResult,
    ScheduledExecution,
    format_identifier,
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
class RunRecord:
    """The context of one execution: identity + command + outcome, no metric.

    Samples join to a RunRecord by the shared (suite, benchmark, info, run,
    phase) key. A *failed* run is a RunRecord with ``failure is not None`` and
    no associated Samples. Recording every execution structurally lets
    summaries, JSON and stats report *which* runs ran, *what* command, and *why*
    they failed without a magic ``failed`` metric polluting real measurements.

    ``returncode`` follows the ExecutionResult convention: ``124`` = timeout,
    ``-1`` = pre-execution failure, any other ``> 0`` = exit code. ``failure``
    is the failure verdict string (``None`` on success); ``message`` is the last
    non-empty line of stderr/stdout on failure.
    """

    suite: str
    benchmark: str
    info: tuple[tuple[str, str], ...]
    run: int
    phase: Phase
    command: tuple[str, ...]
    returncode: int
    runtime: float | None = None
    failure: str | None = None
    message: str = ""

    def is_failure(self) -> bool:
        return self.failure is not None

    def identifier(self) -> str:
        return format_identifier(self.suite, self.benchmark, self.info,
                                 self.run, self.phase)

    @staticmethod
    def from_result(
        sched: ScheduledExecution, result: ExecutionResult
    ) -> RunRecord:
        return RunRecord(
            suite=sched.suite,
            benchmark=sched.benchmark,
            info=sched.info,
            run=sched.run,
            phase=sched.phase,
            command=sched.execution.command,
            returncode=result.returncode,
            runtime=result.runtime,
            failure=result.failure,
            message=RunRecord._diagnostic_excerpt(result) if result.is_failure() else "",
        )

    @staticmethod
    def _diagnostic_excerpt(result: ExecutionResult, *, max_len: int = 80) -> str:
        for text in (result.stderr, result.stdout):
            if not text:
                continue
            for line in reversed(text.splitlines()):
                stripped = line.strip()
                if stripped:
                    return stripped[:max_len] + ("…" if len(stripped) > max_len else "")
        return "(no output)"


@dataclass(slots=True)
class Report:
    """The accumulating Samples and RunRecords plus optional metadata."""

    samples: list[Sample] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    runs: list[RunRecord] = field(default_factory=list)

    @property
    def failures(self) -> list[RunRecord]:
        return [r for r in self.runs if r.is_failure()]

    def metrics(self) -> list[str]:
        return list({s.metric: None for s in self.samples})

    def info_keys(self) -> list[str]:
        return info_keys(self.samples)

    def extend(self, samples: Iterable[Sample]) -> None:
        self.samples.extend(samples)

    def add_run(self, run: RunRecord) -> None:
        self.runs.append(run)

    def record(
        self,
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: Iterable[Sample],
    ) -> None:
        """Ingest one execution: keep its samples and a RunRecord of its outcome."""
        self.samples.extend(samples)
        self.runs.append(RunRecord.from_result(sched, result))


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
        "runs": [
            {
                "suite": r.suite,
                "benchmark": r.benchmark,
                "info": list(r.info),
                "run": r.run,
                "phase": r.phase,
                "command": list(r.command),
                "returncode": r.returncode,
                **({"runtime": r.runtime} if r.runtime is not None else {}),
                **({"failure": r.failure} if r.failure else {}),
                **({"message": r.message} if r.message else {}),
            }
            for r in report.runs
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
    runs: list[RunRecord] = []
    for rd in d.get("runs", []):
        info = tuple((k, v) for k, v in rd.get("info", []))
        runs.append(
            RunRecord(
                suite=rd["suite"],
                benchmark=rd["benchmark"],
                info=info,
                run=rd["run"],
                phase=rd.get("phase", "measure"),
                command=tuple(rd.get("command", ())),
                returncode=rd["returncode"],
                runtime=rd.get("runtime"),
                failure=rd.get("failure"),
                message=rd.get("message", ""),
            )
        )
    return Report(
        samples=samples, metadata=d.get("metadata", {}), runs=runs
    )
