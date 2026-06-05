"""benchr — a small algebraic grammar for benchmarking.

Public surface (intended user API only):

    from benchr import (
        # Path convenience
        Path,

        # Atoms (pipeline data types)
        Execution, ExecutionResult, ScheduledExecution, Phase, Verdict,
        Sample, RunRecord, Report, report_from_json, report_to_json,

        # Processors
        Processor, PartialSample, P,

        # Stopping policies
        StoppingPolicy, PolicyState,
        FixedRuns, CoefficientOfVariation, Custom,

        # Benchmark / Suite
        Benchmark, bench, B,
        Suite, suite,

        # Runners
        Runner, Sequential, Parallel, Dry,

        # Reporters
        Reporter, Mixed, Csv, Json, Dir, Table, Summary, Progress,

        # Formatters
        Formatter, DefaultSummary, Compact,

        # CLI entry points
        run, main,
    )

Internal helpers (concrete Processor subclasses, stats functions, dataclass
arg glue, subprocess helpers) live in their submodules and are not re-exported
here. Reach them directly when needed (e.g. ``from benchr.report.stats import
build_summary``); the package surface is reserved for the user-facing API.
"""

from pathlib import Path

# Atoms
from benchr.grammar.execution import (
    Execution,
    ExecutionResult,
    Phase,
    ScheduledExecution,
    Verdict,
)
from benchr.report.sample import (
    Report,
    RunRecord,
    Sample,
    report_from_json,
    report_to_json,
)

# Processors
from benchr.grammar.processor import (
    P,
    PartialSample,
    Processor,
)

# Stopping policies
from benchr.grammar.policy import (
    CoefficientOfVariation,
    Custom,
    FixedRuns,
    PolicyState,
    StoppingPolicy,
)

# Benchmark / Suite
from benchr.grammar.benchmark import Benchmark, bench
from benchr.grammar.suite import Suite, suite

# Runners
from benchr.runner.base import Runner
from benchr.runner.dry import Dry
from benchr.runner.parallel import Parallel
from benchr.runner.sequential import Sequential

# Reporters
from benchr.report.reporter import (
    Csv,
    Dir,
    Json,
    Mixed,
    Progress,
    Reporter,
    Summary,
    Table,
)

# Formatters
from benchr.report.formatter import Compact, DefaultSummary, Formatter

# CLI
from benchr.cli import main, run

# Aliases
B = bench

__all__ = [
    "Path",
    # Atoms
    "Execution", "ExecutionResult", "ScheduledExecution", "Phase", "Verdict",
    "Sample", "RunRecord", "Report", "report_from_json", "report_to_json",
    # Processors
    "Processor", "PartialSample", "P",
    # Policies
    "StoppingPolicy", "PolicyState",
    "FixedRuns", "CoefficientOfVariation", "Custom",
    # Benchmark / Suite
    "Benchmark", "bench", "B",
    "Suite", "suite",
    # Runners
    "Runner", "Sequential", "Parallel", "Dry",
    # Reporters
    "Reporter", "Mixed", "Csv", "Json", "Dir", "Table", "Summary", "Progress",
    # Formatters
    "Formatter", "DefaultSummary", "Compact",
    # CLI
    "run", "main",
]
