"""Sample and Report: one abstraction (RunRecord) over one execution.

A ``RunRecord`` is the record of one ``ScheduledExecution`` — its identity,
command, outcome, and the parsed metric ``Sample``s. A ``Sample`` carries only
the metric data (``metric, value, unit, lower_is_better``); its identity is
the enclosing ``RunRecord``. A failed run is a RunRecord with ``failure`` set
and an empty ``samples`` list.

All are pure data — no references to live Execution/Metric objects — so they
round-trip through JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from benchr.grammar.execution import (
    ExecutionResult,
    Phase,
    ScheduledExecution,
    Variant,
    format_identifier,
)


@dataclass(frozen=True, slots=True)
class Sample:
    """One parsed metric value. Identity lives on the enclosing RunRecord."""

    metric: str
    value: float
    unit: str = ""
    lower_is_better: bool | None = None


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One execution: identity + command + outcome + parsed samples.

    ``variant`` is a canonical (sorted) tuple of ``(axis, value)`` pairs
    identifying the matrix cell; ``variant_label`` is its human-readable name.

    ``phase`` is ``"warmup"`` or ``"measure"``. Stats default to excluding
    warmup; raw outputs (JSON, CSV, dir) keep both.

    ``returncode`` conventions follow ExecutionResult: ``124`` = timeout,
    ``-1`` = pre-execution failure, any other ``> 0`` = exit code. ``failure``
    is the failure verdict string (``None`` on success); ``message`` is the
    last non-empty line of stderr/stdout on failure. A failed run carries
    ``samples = []``.
    """

    suite: str
    benchmark: str
    variant: Variant
    run: int
    phase: Phase
    command: tuple[str, ...]
    returncode: int
    runtime: float | None = None
    failure: str | None = None
    message: str = ""
    variant_label: str = ""
    samples: list[Sample] = field(default_factory=list)

    def is_failure(self) -> bool:
        return self.failure is not None

    def identifier(self) -> str:
        return format_identifier(self.suite, self.benchmark, self.variant,
                                 self.run, self.phase,
                                 variant_label=self.variant_label)

    @staticmethod
    def from_result(
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: Iterable[Sample] = (),
    ) -> RunRecord:
        return RunRecord(
            suite=sched.suite,
            benchmark=sched.benchmark,
            variant=sched.variant,
            variant_label=sched.variant_label,
            run=sched.run,
            phase=sched.phase,
            command=sched.execution.command,
            returncode=result.returncode,
            runtime=result.runtime,
            failure=result.failure,
            message=RunRecord._diagnostic_excerpt(result) if result.is_failure() else "",
            samples=list(samples),
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


def variant_keys(runs: Iterable[RunRecord]) -> list[str]:
    """Stable list of variant-axis names across a stream of runs."""
    seen: dict[str, None] = {}
    for r in runs:
        for k, _ in r.variant:
            seen.setdefault(k, None)
    return list(seen)


@dataclass(slots=True)
class Report:
    """The accumulating RunRecords (each carrying its parsed Samples)."""

    runs: list[RunRecord] = field(default_factory=list)

    @property
    def failures(self) -> list[RunRecord]:
        return [r for r in self.runs if r.is_failure()]

    def metrics(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.runs:
            for s in r.samples:
                seen.setdefault(s.metric, None)
        return list(seen)

    def variant_keys(self) -> list[str]:
        return variant_keys(self.runs)

    def record(
        self,
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: Iterable[Sample],
    ) -> None:
        """Ingest one execution as a single RunRecord with its samples nested."""
        self.runs.append(RunRecord.from_result(sched, result, samples))


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def report_to_json(report: Report, *, indent: int = 2) -> str:
    return json.dumps(_to_dict(report), indent=indent)


def report_from_json(text: str) -> Report:
    return _from_dict(json.loads(text))


def _to_dict(report: Report) -> dict[str, Any]:
    return {
        "runs": [
            {
                "suite": r.suite,
                "benchmark": r.benchmark,
                "variant": list(r.variant),
                **({"variant_label": r.variant_label} if r.variant_label else {}),
                "run": r.run,
                "phase": r.phase,
                "command": list(r.command),
                "returncode": r.returncode,
                **({"runtime": r.runtime} if r.runtime is not None else {}),
                **({"failure": r.failure} if r.failure else {}),
                **({"message": r.message} if r.message else {}),
                "samples": [
                    {
                        "metric": s.metric,
                        "value": s.value,
                        **({"unit": s.unit} if s.unit else {}),
                        **(
                            {"lower_is_better": s.lower_is_better}
                            if s.lower_is_better is not None
                            else {}
                        ),
                    }
                    for s in r.samples
                ],
            }
            for r in report.runs
        ],
    }


def _from_dict(d: dict[str, Any]) -> Report:
    runs: list[RunRecord] = []
    for rd in d.get("runs", []):
        variant = tuple((k, v) for k, v in rd.get("variant", []))
        samples = [
            Sample(
                metric=sd["metric"],
                value=sd["value"],
                unit=sd.get("unit", ""),
                lower_is_better=sd.get("lower_is_better"),
            )
            for sd in rd.get("samples", [])
        ]
        runs.append(
            RunRecord(
                suite=rd["suite"],
                benchmark=rd["benchmark"],
                variant=variant,
                variant_label=rd.get("variant_label", ""),
                run=rd["run"],
                phase=rd.get("phase", "measure"),
                command=tuple(rd.get("command", ())),
                returncode=rd["returncode"],
                runtime=rd.get("runtime"),
                failure=rd.get("failure"),
                message=rd.get("message", ""),
                samples=samples,
            )
        )
    return Report(runs=runs)
