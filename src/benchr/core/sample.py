"""Sample, Observation, Run, Report: the data model over benchmark execution.

A ``Run`` is one process execution — its identity, command, outcome
(returncode / runtime / failure / stdout / stderr) and the ``Observation``s
measured during it. An ``Observation`` is one measurement point: a bag of
``Sample``s (possibly several metrics) plus an optional per-observation failure.
A command benchmark yields one Run with one Observation; a harness yields one
Run with many. A ``Report`` is the collection of Runs; it summarizes by
flattening every observation's samples per metric.

All are pure data and round-trip through JSON. ``stdout``/``stderr``/``env`` are
kept on a Run for live reporters but excluded from JSON by default (see
``report_to_json``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cattrs import structure, unstructure

from benchr.core.execution import Variant, format_identifier, record_key


@dataclass(frozen=True, slots=True)
class Sample:
    """One parsed metric value. Identity lives on the enclosing Run."""

    metric: str
    value: float
    unit: str = ""
    lower_is_better: bool | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    """One measurement point: samples (possibly multi-metric) + optional failure.

    A failed observation — extraction produced nothing it expected, or the
    harness flagged the iteration — carries ``failure`` and usually no samples;
    the run proceeds to the next observation. ``label`` is the benchmark-variant
    display identifier, carried for live progress reporting.
    """

    samples: list[Sample] = field(default_factory=list[Sample])
    failure: str | None = None
    label: str = ""

    def is_failure(self) -> bool:
        return self.failure is not None


@dataclass(frozen=True, slots=True)
class Run:
    """One process execution: identity + command + outcome + observations.

    ``command``/``cwd``/``env`` are the execution inputs; ``returncode`` /
    ``runtime`` / ``failure`` / ``message`` / ``stdout`` / ``stderr`` the
    outcome; ``observations`` the measurements taken during the run (command: 1;
    harness: N). ``run`` is the run's index within its variant (command runs are
    numbered 1..N; a harness is a single run).

    ``returncode`` conventions follow ExecutionResult: ``124`` = timeout, ``-1``
    = pre-execution failure. ``message`` is the last non-empty stderr/stdout line
    on failure. ``stdout``/``stderr``/``env`` are not serialized to JSON by
    default.
    """

    suite: str
    benchmark: str
    variant: Variant = ()
    variant_label: str = ""
    run: int = 1
    command: tuple[str, ...] = ()
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict[str, str])
    returncode: int = 0
    runtime: float | None = None
    failure: str | None = None
    message: str = ""
    stdout: str = ""
    stderr: str = ""
    observations: list[Observation] = field(default_factory=list[Observation])

    def is_failure(self) -> bool:
        return self.failure is not None

    def identifier(self) -> str:
        return format_identifier(self.suite, self.benchmark, self.variant,
                                 self.run, variant_label=self.variant_label)

    def key(self) -> str:
        """Canonical benchmark-variant key (see ``record_key``)."""
        return record_key(self.suite, self.benchmark, self.variant)


def diagnostic_excerpt(stdout: str, stderr: str, *, max_len: int = 80) -> str:
    """Last non-empty line of stderr (then stdout), truncated — for failures."""
    for text in (stderr, stdout):
        if not text:
            continue
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped:
                return stripped[:max_len] + ("…" if len(stripped) > max_len else "")
    return "(no output)"


@dataclass(slots=True)
class Report:
    """The accumulating Runs, each carrying its Observations.

    ``warmups`` maps a benchmark-variant key (``record_key``) to the number of
    its leading *observations* that were warmup — recorded once per variant.
    Stats drop those observations by default.
    """

    runs: list[Run] = field(default_factory=list[Run])
    warmups: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def failures(self) -> list[Run]:
        """Runs whose process failed (returncode-bearing failures)."""
        return [r for r in self.runs if r.is_failure()]

    def observations(self) -> list[Observation]:
        return [o for r in self.runs for o in r.observations]

    def metrics(self) -> list[str]:
        """Distinct metric names, first-seen order."""
        return list(dict.fromkeys(
            s.metric for r in self.runs for o in r.observations for s in o.samples))

    def variant_keys(self) -> list[str]:
        """Stable list of matrix-dimension names across all runs, first-seen order."""
        return list(dict.fromkeys(k for r in self.runs for k, _ in r.variant))

    def add(self, run: Run) -> None:
        self.runs.append(run)

    def warmup(self, key: str, observations: int) -> None:
        """Note that benchmark-variant ``key``'s first ``observations`` were warmup."""
        if observations:
            self.warmups[key] = observations


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

_OUTPUT_FIELDS = ("stdout", "stderr", "env")


def report_to_json(report: Report, *, indent: int = 2, include_output: bool = False) -> str:
    """Serialize a Report. ``stdout``/``stderr``/``env`` are dropped unless
    ``include_output`` (they bloat the file and are rarely needed offline)."""
    raw = unstructure(report)
    if not include_output:
        for run in raw.get("runs", []):
            for f in _OUTPUT_FIELDS:
                run.pop(f, None)
    return json.dumps(raw, indent=indent)


def report_from_json(text: str) -> Report:
    return structure(json.loads(text), Report)
