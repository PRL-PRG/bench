"""benchr — a small algebraic grammar for benchmarking.

Public surface:

    from benchr import (
        # Atoms
        Execution, ProcessResult, SuccessfulProcessResult, FailedProcessResult,
        ScheduledExecution, Sample, Report,

        # Processors
        Processor, P,
        PartialSample,

        # Stopping policies
        StoppingPolicy, PolicyState,
        FixedRuns, CoefficientOfVariation, Custom,

        # Benchmark / Suite
        Benchmark, Suite, bench, suite,

        # Runners
        Sequential, Parallel, Dry,
        execute,                 # low-level subprocess helper

        # Reporters
        Reporter, Mixed, Csv, Json, Dir, Table, Summary,
        console, err_console,

        # Stats / Formatters
        group, build_summary, scale_unit, geomean_with_sigma,
        Formatter, DefaultSummary, Compact,

        # CLI helpers
        run, main,
    )

Plus convenience re-exports: ``Path``, ``B`` (alias of ``bench``).
"""

from pathlib import Path

# Atoms
from benchr.grammar.execution import (
    Execution,
    FailedProcessResult,
    Phase,
    ProcessResult,
    ScheduledExecution,
    SuccessfulProcessResult,
)
from benchr.report.sample import (
    Report,
    Sample,
    info_keys,
    report_from_json,
    report_to_json,
)

# Processors
from benchr.grammar.processor import (
    Constant,
    Fail,
    FloatPerLine,
    P,
    PartialSample,
    Processor,
    RUsage,
    Regex,
    Rebench,
    Time,
    stamp,
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
from benchr.grammar.suite import Suite, benchmark_info, suite

# Context
from benchr.grammar.context import add_dataclass_args, build_dataclass

# Runners
from benchr.runner.base import Runner, execute, plan
from benchr.runner.dry import Dry
from benchr.runner.parallel import Parallel
from benchr.runner.sequential import Sequential

# Reporters
from benchr.report.reporter import (
    Csv,
    Dir,
    Json,
    Mixed,
    Reporter,
    Summary,
    Table,
    console,
    err_console,
)

# Stats / Formatters
from benchr.report.formatter import Compact, DefaultSummary, Formatter
from benchr.report.stats import (
    BenchmarkGroup,
    BenchmarkId,
    GeoMeanRatio,
    GroupStats,
    GroupedReport,
    MetricKey,
    MetricRatio,
    MetricStats,
    RunCounts,
    SummaryData,
    build_summary,
    geomean_with_sigma,
    group,
    metric_ratio,
    metric_stats,
    scale_unit,
)

# CLI
from benchr.cli import main, run

# Aliases
B = bench

__all__ = [
    "Path",
    # Atoms
    "Execution", "ProcessResult", "SuccessfulProcessResult", "FailedProcessResult",
    "ScheduledExecution", "Phase",
    "Sample", "Report", "info_keys", "report_to_json", "report_from_json",
    # Processors
    "Processor", "PartialSample", "stamp", "P",
    "FloatPerLine", "Regex", "Rebench", "RUsage", "Time", "Constant", "Fail",
    # Policies
    "StoppingPolicy", "PolicyState",
    "FixedRuns", "CoefficientOfVariation", "Custom",
    # Benchmark / Suite
    "Benchmark", "bench", "B",
    "Suite", "suite", "benchmark_info",
    # Context
    "add_dataclass_args", "build_dataclass",
    # Runners
    "Runner", "execute", "plan",
    "Sequential", "Parallel", "Dry",
    # Reporters
    "Reporter", "Mixed", "Csv", "Json", "Dir", "Table", "Summary",
    "console", "err_console",
    # Stats / Formatters
    "BenchmarkGroup", "BenchmarkId", "GroupedReport", "GroupStats",
    "MetricKey", "MetricStats", "MetricRatio", "GeoMeanRatio",
    "RunCounts", "SummaryData",
    "group", "build_summary", "scale_unit", "geomean_with_sigma",
    "metric_stats", "metric_ratio",
    "Formatter", "DefaultSummary", "Compact",
    # CLI
    "run", "main",
]
