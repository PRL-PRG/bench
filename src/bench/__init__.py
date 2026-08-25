"""bench - a benchmarking framework."""

# Atoms
# Benchmark / SuiteBuilder
from bench.builder.benchmark import (
    Benchmark,
    BenchmarkBuilder,
    bench,
    default_label,
    from_files,
)
from bench.builder.context import Context, SharedBenchParams, SharedSelectionParams
from bench.builder.suite import SuiteBuilder, suite

# CLI
from bench.cli import main
from bench.core.checks import run_checks

# Fingerprint + diagnostics
from bench.core.fingerprint import (
    Diagnostic,
    Fingerprint,
    NoProbe,
    Probe,
    SystemProbe,
)
from bench.core.invocation import (
    Invocation,
    InvocationResult,
    Variant,
    Verdict,
    default_success,
)

# Metrics
from bench.core.metric import (
    FloatPerLine,
    IterationMetric,
    Metric,
    RebenchMetric,
    RegexMetric,
    RUsage,
    Time,
    max_rss,
)

# Outlier detection
from bench.core.outlier import (
    ModifiedZScore,
    NoDetection,
    OutlierDetection,
)

# Stopping policies
from bench.core.policy import (
    CoefficientOfVariation,
    FixedRuns,
    MaxDuration,
    PolicyState,
    StoppingPolicy,
)
from bench.core.results import (
    Execution,
    Iteration,
    Report,
    Sample,
    report_from_json,
    report_to_json,
)
from bench.perf import PerfStat

# Formatters
from bench.report.formatter import (
    Compact,
    DefaultSummary,
    Formatter,
    GeomeanSummary,
    Results,
    Summary,
)

# Reporters
from bench.report.reporter import (
    CompositeReporter,
    CsvReporter,
    DirReporter,
    JsonReporter,
    ProgressReporter,
    Reporter,
    SummaryReporter,
    execution_dir,
    variant_path,
)

# BenchAppBuilder abstraction + run pipeline
from bench.run import BenchAppBuilder, NoBenchmarksMatchedError, bench_app, run

# Runners
from bench.runner.base import (
    Runner,
    SuiteMaterializationError,
)
from bench.runner.dry import DryRunner
from bench.runner.parallel import Parallel
from bench.runner.sequential import SequentialRunner

# Errors
from bench.utils import BenchError

__all__ = [
    # Atoms
    "Invocation",
    "InvocationResult",
    "Variant",
    "Verdict",
    "default_success",
    "Sample",
    "Iteration",
    "Execution",
    "Report",
    "report_from_json",
    "report_to_json",
    # Metrics
    "IterationMetric",
    "Metric",
    "Time",
    "RegexMetric",
    "FloatPerLine",
    "RebenchMetric",
    "RUsage",
    "max_rss",
    "PerfStat",
    # Policies
    "StoppingPolicy",
    "PolicyState",
    "FixedRuns",
    "CoefficientOfVariation",
    "MaxDuration",
    # Outlier detection
    "OutlierDetection",
    "NoDetection",
    "ModifiedZScore",
    # Fingerprint + diagnostics
    "Fingerprint",
    "Probe",
    "SystemProbe",
    "NoProbe",
    "Diagnostic",
    "run_checks",
    # Benchmark / SuiteBuilder
    "Benchmark",
    "BenchmarkBuilder",
    "bench",
    "default_label",
    "from_files",
    "SuiteBuilder",
    "suite",
    "Context",
    "SharedBenchParams",
    "SharedSelectionParams",
    # Runners
    "Runner",
    "SequentialRunner",
    "Parallel",
    "DryRunner",
    # Errors
    "BenchError",
    "SuiteMaterializationError",
    # Reporters
    "Reporter",
    "CompositeReporter",
    "CsvReporter",
    "JsonReporter",
    "DirReporter",
    "SummaryReporter",
    "ProgressReporter",
    "execution_dir",
    "variant_path",
    # Formatters
    "Formatter",
    "Results",
    "Summary",
    "GeomeanSummary",
    "DefaultSummary",
    "Compact",
    # BenchAppBuilder + run pipeline
    "BenchAppBuilder",
    "NoBenchmarksMatchedError",
    "bench_app",
    "run",
    # CLI
    "main",
]
