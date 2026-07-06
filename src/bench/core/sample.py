"""The data model over benchmark execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cattrs import structure, unstructure

from bench.core.environment import Diagnostic, Environment
from bench.core.execution import Variant, format_identifier, record_key


@dataclass(frozen=True, slots=True)
class Sample:
    """One parsed metric value such as a time in seconds. Belongs to an Iteration."""

    metric: str
    value: float
    unit: str = ""
    lower_is_better: bool | None = None
    outlier: bool = False  # flagged by outlier detection and kept in stats


@dataclass(frozen=True, slots=True)
class Iteration:
    """One measurement. A command benchmark produces one Iteration per Execution,
    a harness produces many. Holds the parsed Samples and an optional failure."""

    samples: list[Sample] = field(default_factory=list[Sample])
    failure: str | None = None
    runtime: float = 0.0  # command runtime that produced this iteration (s)
    warmup: bool = False  # a discarded warmup iteration, flagged by the Controller

    def is_failure(self) -> bool:
        return self.failure is not None


@dataclass(frozen=True, slots=True)
class Execution:
    """One subprocess run start to finish. Holds the Iterations measured from it,
    one for a command benchmark and many for a harness, plus any whole-process Samples."""

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
    iterations: list[Iteration] = field(default_factory=list[Iteration])
    process_samples: list[Sample] = field(default_factory=list[Sample])

    def is_failure(self) -> bool:
        return self.failure is not None

    def identifier(self) -> str:
        return format_identifier(
            self.suite,
            self.benchmark,
            self.variant,
            self.run,
            variant_label=self.variant_label,
        )

    def key(self) -> str:
        """Canonical benchmark-variant key (see `record_key`)."""
        return record_key(self.suite, self.benchmark, self.variant)


def diagnostic_excerpt(stdout: str, stderr: str, *, max_len: int = 80) -> str:
    """Last non-empty line of stderr (then stdout), truncated, for failures."""
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
    """All Executions from a benchmarking session, plus the machine environment
    and diagnostics."""

    executions: list[Execution] = field(default_factory=list[Execution])
    environment: Environment | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list[Diagnostic])

    @property
    def failures(self) -> list[Execution]:
        """Executions whose process failed (returncode-bearing failures)."""
        return [ex for ex in self.executions if ex.is_failure()]

    def iterations(self) -> list[Iteration]:
        return [it for ex in self.executions for it in ex.iterations]

    def metrics(self) -> list[str]:
        """Distinct metric names across iterations and whole-process samples,
        first-seen order."""
        return list(
            dict.fromkeys(
                s.metric
                for ex in self.executions
                for s in (
                    *(s for it in ex.iterations for s in it.samples),
                    *ex.process_samples,
                )
            )
        )

    def variant_keys(self) -> list[str]:
        """Stable list of matrix-dimension names across all executions, first-seen order."""
        return list(dict.fromkeys(k for ex in self.executions for k, _ in ex.variant))

    def add(self, execution: Execution) -> None:
        self.executions.append(execution)


_OUTPUT_FIELDS = ("stdout", "stderr", "env")


def report_to_json(
    report: Report, *, indent: int = 2, include_output: bool = False
) -> str:
    """Serialize a Report. `stdout`/`stderr`/`env` are dropped unless
    `include_output`."""
    raw = unstructure(report)
    if not include_output:
        for ex in raw.get("executions", []):
            for f in _OUTPUT_FIELDS:
                ex.pop(f, None)
    return json.dumps(raw, indent=indent)


def report_from_json(text: str) -> Report:
    return structure(json.loads(text), Report)
