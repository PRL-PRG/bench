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
from collections.abc import Iterable
from dataclasses import dataclass, field

from cattrs import structure, unstructure

from benchr.core.execution import (
    ExecutionResult,
    ScheduledExecution,
    Variant,
    format_identifier,
    record_key,
)


@dataclass(frozen=True, slots=True)
class Sample:
    """One parsed metric value. Identity lives on the enclosing RunRecord."""

    metric: str
    value: float
    unit: str = ""
    lower_is_better: bool | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    """Outcome of one run/iteration, identity-free. The Controller stamps
    identity (suite/benchmark/variant/run) to make a RunRecord."""

    samples: list[Sample]
    returncode: int = 0
    runtime: float | None = None
    failure: str | None = None
    message: str = ""

    def is_failure(self) -> bool:
        return self.failure is not None


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One execution: identity + command + outcome + parsed samples.

    ``variant`` is a canonical (sorted) tuple of ``(dimension, value)`` pairs
    identifying the matrix cell; ``variant_label`` is its human-readable name.

    Run numbers are continuous: a benchmark's warmup runs are 1..W, measured
    runs follow. Records carry no warmup marking — ``Report.warmups`` holds W
    per benchmark variant, and stats default to dropping the first W runs.

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
    command: tuple[str, ...]
    returncode: int
    runtime: float | None = None
    failure: str | None = None
    message: str = ""
    variant_label: str = ""
    samples: list[Sample] = field(default_factory=list[Sample])

    def is_failure(self) -> bool:
        return self.failure is not None

    def identifier(self) -> str:
        return format_identifier(self.suite, self.benchmark, self.variant,
                                 self.run, variant_label=self.variant_label)

    def key(self) -> str:
        """Canonical benchmark-variant key (see ``record_key``)."""
        return record_key(self.suite, self.benchmark, self.variant)

    @staticmethod
    def from_run_result(template: ScheduledExecution, run: int, rr: RunResult) -> RunRecord:
        return RunRecord(
            suite=template.suite,
            benchmark=template.benchmark,
            variant=template.variant,
            variant_label=template.variant_label,
            run=run,
            command=template.execution.command,
            returncode=rr.returncode,
            runtime=rr.runtime,
            failure=rr.failure,
            message=rr.message,
            samples=list(rr.samples),
        )


def diagnostic_excerpt(result: ExecutionResult, *, max_len: int = 80) -> str:
    for text in (result.stderr, result.stdout):
        if not text:
            continue
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped:
                return stripped[:max_len] + ("…" if len(stripped) > max_len else "")
    return "(no output)"


def variant_keys(runs: Iterable[RunRecord]) -> list[str]:
    """Stable list of matrix-dimension names across a stream of runs."""
    return list(dict.fromkeys(k for r in runs for k, _ in r.variant))


@dataclass(slots=True)
class Report:
    """The accumulating RunRecords (each carrying its parsed Samples).

    ``warmups`` maps a benchmark-variant key (``record_key``) to the number
    of its leading runs that were warmup — recorded once per variant, not on
    each record. Stats drop those runs by default.
    """

    runs: list[RunRecord] = field(default_factory=list[RunRecord])
    warmups: dict[str, int] = field(default_factory=dict[str, int])
    metadata: dict[str, list[Sample]] = field(default_factory=dict[str, list[Sample]])

    @property
    def failures(self) -> list[RunRecord]:
        return [r for r in self.runs if r.is_failure()]

    def metrics(self) -> list[str]:
        """Distinct metric names, first-seen order."""
        return list(dict.fromkeys(
            s.metric for r in self.runs for s in r.samples))

    def variant_keys(self) -> list[str]:
        return variant_keys(self.runs)

    def add(self, rec: RunRecord) -> None:
        """Append one RunRecord."""
        self.runs.append(rec)

    def warmup(self, key: str, runs: int) -> None:
        """Note that benchmark-variant ``key``'s (see ``record_key``) first
        ``runs`` runs were warmup."""
        if runs:
            self.warmups[key] = runs

    def set_metadata(self, key: str, samples: list[Sample]) -> None:
        """Set benchmark-variant ``key``'s whole-process metadata samples."""
        self.metadata[key] = samples


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def report_to_json(report: Report, *, indent: int = 2) -> str:
    return json.dumps(unstructure(report), indent=indent)


def report_from_json(text: str) -> Report:
    raw = json.loads(text)
    # Compat: pre-v4 reports stamped each run with a phase; drop their warmup
    # runs so old baselines still compare correctly. Remove once old baseline
    # files are retired.
    raw["runs"] = [r for r in raw["runs"] if r.pop("phase", None) != "warmup"]
    raw.setdefault("metadata", {})
    return structure(raw, Report)
