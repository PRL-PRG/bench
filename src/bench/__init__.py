"""bench - a benchmarking framework."""

# CLI
from bench.__main__ import main

# Builder
from bench.builder import (
    BenchAppBuilder,
    BenchmarkBuilder,
    Context,
    NoBenchmarksMatchedError,
    SuiteBuilder,
    SuiteMaterializationError,
    bench,
    bench_app,
    default_label,
    default_success,
    from_files,
    run,
    suite,
)
from bench.core.diagnostic import Diagnostic, run_checks

# Fingerprint + diagnostics
from bench.core.fingerprint import (
    Fingerprint,
    NoProbe,
    Probe,
    SystemProbe,
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

# Errors
from bench.error import BenchError
from bench.model.benchmark import (
    Benchmark,
    Variant,
)
from bench.model.invocation import (
    Invocation,
    InvocationResult,
    Verdict,
)
from bench.model.results import (
    Execution,
    Iteration,
    Report,
    Sample,
    report_from_json,
    report_to_json,
)
from bench.params import SharedBenchParams, SharedSelectionParams
from bench.perf import PerfStat

# Reporters
from bench.report import (
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

# Runners
from bench.runner import (
    DryRunner,
    Parallel,
    Runner,
    SequentialRunner,
)

# Formatters
from bench.summary.formatter import (
    Compact,
    DefaultSummary,
    Formatter,
    GeomeanSummary,
    Results,
    Summary,
)

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
