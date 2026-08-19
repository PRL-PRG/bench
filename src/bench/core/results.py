"""The data model over benchmark execution."""

from __future__ import annotations

import dataclasses
import itertools
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from cattrs import structure, unstructure

from bench.core.environment import Diagnostic, Environment
from bench.core.invocation import Variant, format_identifier
from bench.core.process import Command

# TODO: Move to model
type Direction = Literal["lower better", "higher better", "uncomparable"]


@dataclass(frozen=True, slots=True)
class Sample:
    """One parsed metric value such as a time in seconds. Belongs to an Iteration."""

    metric: str
    value: float
    unit: str = ""
    direction: Direction = "uncomparable"

    iteration: int | None = None
    "Process sample if `None`, otherwise it belongs to an iteration"

    extra: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class Iteration:
    """One measurement. A command benchmark produces one Iteration per Execution,
    a harness produces many. Holds the parsed Samples and an optional failure."""

    samples: list[Sample] = field(default_factory=list[Sample])
    warmup: bool = False  # a discarded warmup iteration, flagged by the Controller

    def add_sample(self, sample: Sample) -> Iteration:
        return dataclasses.replace(self, samples=self.samples + [sample])


@dataclass(frozen=True, slots=True)
class Execution:
    """One subprocess run start to finish. Holds the Iterations measured from it,
    one for a command benchmark and many for a harness, plus any whole-process Samples."""

    suite: str
    benchmark: str
    runtime: float
    variant: Variant = Variant()
    variant_label: str = ""
    run: int = 1
    command: Command = ()
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict[str, str])
    returncode: int = 0
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

    def metrics(self) -> list[str]:
        """Distinct metric names across iterations and whole-process samples,
        first-seen order."""
        return list(
            dict.fromkeys(
                s.metric
                for ex in self.executions
                for s in itertools.chain(
                    (s for it in ex.iterations for s in it.samples),
                    ex.process_samples,
                )
            )
        )

    def variant_keys(self) -> list[str]:
        """Stable list of matrix-dimension names across all executions, first-seen order."""
        res = list[str]()
        for ex in self.executions:
            for k in ex.variant.keys():
                if k not in res:
                    res.append(k)

        return res

    def samples_extra_keys(self) -> list[str]:
        iter_samples = (
            s for e in self.executions for i in e.iterations for s in i.samples
        )
        process_samples = (s for e in self.executions for s in e.process_samples)
        samples = itertools.chain(iter_samples, process_samples)

        return list(set(k for s in samples for k in s.extra.keys()))

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
