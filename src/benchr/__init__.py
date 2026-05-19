"""benchr -- a lightweight Python benchmarking framework."""

from benchr.types import (
    const,
    Env,
    Command,
    Parameters,
    SuccessfulProcessResult,
    FailedProcessResult,
    ProcessResult,
    Execution,
    Benchmark,
)
from benchr.suites import (
    BenchmarkCollection,
    Suite,
    BaseSuite,
    suite,
    SuiteDecorator,
    MatrixSuite,
    Matrix,
    RunsSuite,
    TimeoutSuite,
    Config,
)
from benchr.results import (
    Measurement,
    ExecutionResult,
    execution_result_to_json,
    execution_result_from_json,
    MetricKey,
    VariantInfo,
    BenchmarkRunCounts,
    BenchmarkGroup,
    GroupedResult,
    BenchmarkId,
    MetricStats,
    GroupStats,
    MetricRatio,
    GeoMeanRatio,
    SummaryData,
    build_summary_data,
    build_summary_data_from_grouped,
    scale_unit,
    geomean_with_sigma,
    group_execution_result,
    extract_unique_names,
)
from benchr.parsers import (
    ResultParser,
    MixedResultParser,
    PlainFloatParser,
    LineParser,
    RegexParser,
    RebenchParser,
    SingleResourceUsageParser,
    MaxRssParser,
    TimeParser,
    FailedParser,
    IgnoreFailParserDecorator,
    DirectionParserDecorator,
)
from benchr.output import (
    console,
    err_console,
    Reporter,
    MixedReporter,
    CsvReporter,
    JsonReporter,
    TableReporter,
    SummaryFormatter,
    DefaultSummaryFormatter,
    CompactFormatter,
    SummaryReporter,
    DirReporter,
    compare_and_print,
    Executor,
    DefaultExecutor,
    ParallelExecutor,
    DryExecutor,
    make_argparser,
    parse_params,
    main,
)

from pathlib import Path

B = Benchmark

__all__ = [
    # Definitions
    "Env",
    "Command",
    "Parameters",
    "ProcessResult",
    "SuccessfulProcessResult",
    "FailedProcessResult",
    # Input definitions
    "Execution",
    "Benchmark",
    "B",
    # Suites of benchmarks
    "Suite",
    "suite",
    # Suite decorators
    "SuiteDecorator",
    "MatrixSuite",
    "Matrix",
    "TimeoutSuite",
    # Configuration
    "Config",
    # Result definitions
    "Measurement",
    "ExecutionResult",
    # Serialization
    "execution_result_to_json",
    "execution_result_from_json",
    # Grouped (comparison) types
    "MetricKey",
    "VariantInfo",
    "BenchmarkRunCounts",
    "BenchmarkGroup",
    "GroupedResult",
    # Summary statistics
    "BenchmarkId",
    "MetricStats",
    "GroupStats",
    "MetricRatio",
    "GeoMeanRatio",
    "SummaryData",
    "build_summary_data",
    "build_summary_data_from_grouped",
    "scale_unit",
    "geomean_with_sigma",
    "group_execution_result",
    "extract_unique_names",
    # Parsers
    "ResultParser",
    "MixedResultParser",
    "PlainFloatParser",
    "LineParser",
    "RegexParser",
    "RebenchParser",
    "SingleResourceUsageParser",
    "MaxRssParser",
    "TimeParser",
    "FailedParser",
    # Parser decorators
    "IgnoreFailParserDecorator",
    "DirectionParserDecorator",
    # Console
    "console",
    "err_console",
    # Reporters
    "Reporter",
    "MixedReporter",
    "CsvReporter",
    "JsonReporter",
    "TableReporter",
    "SummaryFormatter",
    "DefaultSummaryFormatter",
    "CompactFormatter",
    "SummaryReporter",
    "DirReporter",
    # Executors
    "Executor",
    "DefaultExecutor",
    "ParallelExecutor",
    "DryExecutor",
    # ArgumentParsing
    "make_argparser",
    "parse_params",
    # Comparison
    "compare_and_print",
    # Default main
    "main",
    # Reexports
    "Path",
]
