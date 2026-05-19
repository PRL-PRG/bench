import abc
import dataclasses
import re
import resource
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Literal, Optional

from benchr.types import (
    ProcessResult,
    SuccessfulProcessResult,
    FailedProcessResult,
    Execution,
)
from benchr.results import Measurement, ExecutionResult


class ResultParser(abc.ABC):
    """
    Parse stdout and stderr into results
    """

    @abc.abstractmethod
    def parse(self, process_result: ProcessResult) -> ExecutionResult: ...

    def ignore_fail(self) -> "ResultParser":
        """
        Ignore failed executions, parsing them as successful
        """
        return IgnoreFailParserDecorator(self)

    def lower_is_better(self) -> "ResultParser":
        """Tag every parsed measurement as a lower-is-better metric."""
        return DirectionParserDecorator(self, lower_is_better=True)

    def higher_is_better(self) -> "ResultParser":
        """Tag every parsed measurement as a higher-is-better metric."""
        return DirectionParserDecorator(self, lower_is_better=False)

    def __and__(self, other) -> "ResultParser":
        return MixedResultParser(self, other)


class MixedResultParser(ResultParser):
    """
    Multiple parsers posing as one
    """

    parsers: list[ResultParser]

    @staticmethod
    def flatten(parsers: Iterable[ResultParser]) -> Iterator[ResultParser]:
        """
        Flatten nested MixedResultParser instances into a single level.
        """
        for parser in parsers:
            if isinstance(parser, MixedResultParser):
                yield from parser.parsers
            else:
                yield parser

    def __init__(self, *parsers: ResultParser) -> None:
        self.parsers = list(MixedResultParser.flatten(parsers))

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        result = ExecutionResult()

        for parser in self.parsers:
            result.measurements += parser.parse(process_result).measurements

        return result


class PlainFloatParser(ResultParser):
    """
    Try to parse simple floats on each line as seconds. Only on successful
    runs.
    """

    unit: str
    metric: str

    def __init__(self, unit: str, metric: str = "runtime") -> None:
        self.unit = unit
        self.metric = metric

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        if isinstance(process_result, FailedProcessResult):
            return ExecutionResult()

        result = ExecutionResult()

        for line in process_result.stdout.split("\n"):
            try:
                value = float(line)
                result.measurements.append(
                    Measurement(
                        execution=process_result.execution,
                        metric=self.metric,
                        value=value,
                        unit=self.unit,
                    )
                )
            except ValueError:
                pass

        return result


class LineParser(ResultParser):
    """
    Extract a single non-empty line from stdout/stderr and pass it to a subparser.

    The line parameter selects which non-empty line to extract:
    - Positive values are 1-based from the top (1 = first, 2 = second, ...)
    - Negative values index from the bottom (-1 = last, -2 = second to last, ...)
    - 0 is forbidden
    """

    subparser: ResultParser
    line: int

    def __init__(self, subparser: ResultParser, line: int = -1) -> None:
        if line == 0:
            raise ValueError(
                "line must be non-zero (positive from top, negative from bottom)"
            )
        self.subparser = subparser
        self.line = line

    @staticmethod
    def _select_line(text: str, line: int) -> str:
        lines = [line for line in text.split("\n") if line.strip()]
        try:
            # Convert 1-based positive index to 0-based
            idx = line - 1 if line > 0 else line
            return lines[idx]
        except IndexError:
            return ""

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        if isinstance(process_result, FailedProcessResult):
            return ExecutionResult()

        return self.subparser.parse(
            dataclasses.replace(
                process_result,
                stdout=self._select_line(process_result.stdout, self.line),
                stderr=self._select_line(process_result.stderr, self.line),
            )
        )


class RegexParser(ResultParser):
    """
    Parse the output of a successful run based on a regex
    """

    type MatchGroup = str | int
    type OutputType = Literal["stdout", "stderr", "both"]

    metric: str
    regex: re.Pattern[str]
    output: OutputType

    match_group: MatchGroup
    process: Callable[[str], float]

    unit: Optional[str]
    unit_match_group: Optional[MatchGroup]

    iterations: bool

    def __init__(
        self,
        metric: str,
        regex: re.Pattern[str],
        output: OutputType,
        match_group: MatchGroup,
        process: Callable[[str], float] = float,
        unit: Optional[str] = None,
        unit_match_group: Optional[MatchGroup] = None,
        iterations: bool = False,
    ) -> None:
        self.metric = metric
        self.regex = regex
        self.output = output

        self.match_group = match_group
        self.process = process

        if unit is None and unit_match_group is None:
            raise ValueError("Missing unit specification")
        self.unit = unit
        self.unit_match_group = unit_match_group

        self.iterations = iterations

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        if isinstance(process_result, FailedProcessResult):
            return ExecutionResult()

        result = ExecutionResult()
        if self.output == "stdout":
            outputs = [process_result.stdout]
        elif self.output == "stderr":
            outputs = [process_result.stderr]
        elif self.output == "both":
            outputs = [process_result.stdout, process_result.stderr]
        else:
            raise ValueError(f"Unknown output type {self.output}")

        for output in outputs:
            pos = 0
            while (match := self.regex.search(output, pos)) is not None:
                pos = match.end()
                value = self.process(match.group(self.match_group))

                if self.unit_match_group is not None:
                    unit = match.group(self.unit_match_group)
                elif self.unit is not None:
                    unit = self.unit
                else:
                    unit = ""

                result.measurements.append(
                    Measurement(
                        process_result.execution,
                        self.metric,
                        value,
                        unit,
                    )
                )

        return result


class RebenchParser(ResultParser):
    """
    Format used by the ReBench (https://github.com/smarr/ReBench) benchmarker,
    mostly copied from the RebenchLogAdapter. The supported format is:
    ```
    optional_prefix: benchmark_name optional_criterion: iterations=123 runtime: 1000[ms|us]
    ```
    or for non-runtime
    ```
    optional_prefix: benchmark_name: criterion: number_with_unit
    ```

    Unlike ReBench, benchr only reports runtime in ms. Runtime report with other
    criterion other than "total" (or none) are ignored.

    When a runtime with no criterion (or criterion "total") or non-runtime
    criterion "total" is parsed, a new iteration is assumed. This should be
    equivalent to ReBench.
    """

    re_log_line = re.compile(
        r"^(?:.*: )?([^\s]+)( [\w\.]+)?: iterations=([0-9]+) "
        + r"runtime: (?P<runtime>(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?)"
        + r"(?P<unit>[mu])s"
    )

    re_extra_criterion_log_line = re.compile(
        r"^(?:.*: )?([^\s]+): (?P<criterion>[^:]{1,30}):\s*"
        + r"(?P<value>(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?)"
        + r"(?P<unit>[a-zA-Z]+)"
    )

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        if process_result.stdout is None:
            return ExecutionResult()

        result = ExecutionResult()

        for line in process_result.stdout.split("\n"):
            match = self.re_log_line.match(line)
            if match is not None:
                # Match runtime
                time = float(match.group("runtime"))
                if match.group("unit") == "u":
                    time /= 1000

                # Match criterion, maybe skip
                criterion = match.group(2)
                if criterion is not None and criterion.strip() != "total":
                    continue

                result.measurements.append(
                    Measurement(
                        process_result.execution,
                        "runtime",
                        time,
                        "ms",
                    )
                )
                continue

            match = self.re_extra_criterion_log_line.match(line)
            if match is not None:
                # Match groups
                value = float(match.group("value"))
                unit = match.group("unit")
                criterion = match.group("criterion")

                # Add measurement
                result.measurements.append(
                    Measurement(
                        process_result.execution,
                        criterion,
                        value,
                        unit,
                    )
                )
                continue

        return result


class SingleResourceUsageParser(ResultParser):
    RUsageField = Literal[
        "ru_utime",
        "ru_stime",
        "ru_maxrss",
        "ru_ixrss",
        "ru_idrss",
        "ru_isrss",
        "ru_minflt",
        "ru_majflt",
        "ru_nswap",
        "ru_inblock",
        "ru_oublock",
        "ru_msgsnd",
        "ru_msgrcv",
        "ru_nsignals",
        "ru_nvcsw",
        "ru_nivcsw",
    ]

    field: RUsageField
    metric: str
    unit: str

    def __init__(self, field: RUsageField, metric: str, unit: str) -> None:
        self.field = field
        self.metric = metric
        self.unit = unit

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        if process_result.rusage is None:
            return ExecutionResult()

        value = getattr(process_result.rusage, self.field)

        # MacOS reports in B, not kB
        if sys.platform == "darwin" and self.field == "ru_maxrss":
            value /= 1024

        return ExecutionResult(
            [
                Measurement(
                    execution=process_result.execution,
                    metric=self.metric,
                    value=value,
                    unit=self.unit,
                )
            ]
        )


def MaxRssParser() -> ResultParser:
    return SingleResourceUsageParser("ru_maxrss", "max_rss", "kB")


class TimeParser(ResultParser):
    """
    Emit up to three time measurements (in seconds): "elapsed" (wall clock,
    from process_result.runtime), "user" (rusage.ru_utime), and
    "system" (rusage.ru_stime). At least one flag must be true.
    """

    elapsed: bool
    system: bool
    user: bool

    def __init__(
        self, elapsed: bool = True, system: bool = False, user: bool = False
    ) -> None:
        if not (elapsed or system or user):
            raise ValueError(
                "TimeParser requires at least one of elapsed, system, user to be True"
            )
        self.elapsed = elapsed
        self.system = system
        self.user = user

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        measurements: list[Measurement] = []

        if self.elapsed and process_result.runtime is not None:
            measurements.append(
                Measurement(
                    execution=process_result.execution,
                    metric="elapsed",
                    value=process_result.runtime,
                    unit="s",
                )
            )

        if process_result.rusage is not None:
            if self.user:
                measurements.append(
                    Measurement(
                        execution=process_result.execution,
                        metric="user",
                        value=process_result.rusage.ru_utime,
                        unit="s",
                    )
                )
            if self.system:
                measurements.append(
                    Measurement(
                        execution=process_result.execution,
                        metric="system",
                        value=process_result.rusage.ru_stime,
                        unit="s",
                    )
                )

        return ExecutionResult(measurements)


class FailedParser(ResultParser):
    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        return ExecutionResult(
            [
                Measurement(
                    execution=process_result.execution,
                    metric="failed",
                    value=1 if isinstance(process_result, FailedProcessResult) else 0,
                )
            ]
        )


class IgnoreFailParserDecorator(ResultParser):
    subparser: ResultParser

    def __init__(self, subparser: ResultParser) -> None:
        self.subparser = subparser

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        if isinstance(process_result, FailedProcessResult):
            process_result = SuccessfulProcessResult(
                execution=process_result.execution,
                runtime=process_result.runtime or -1,
                stdout=process_result.stdout or "",
                stderr=process_result.stderr or "",
                rusage=process_result.rusage,
            )

        return self.subparser.parse(process_result)


class DirectionParserDecorator(ResultParser):
    subparser: ResultParser
    _lower_is_better: bool

    def __init__(self, subparser: ResultParser, lower_is_better: bool) -> None:
        self.subparser = subparser
        self._lower_is_better = lower_is_better

    def parse(self, process_result: ProcessResult) -> ExecutionResult:
        result = self.subparser.parse(process_result)

        for m in result.measurements:
            m.lower_is_better = self._lower_is_better

        return result
