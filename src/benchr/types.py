from __future__ import annotations
import argparse
import dataclasses
import re
import resource
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

if TYPE_CHECKING:
    from benchr.parsers import ResultParser


def const[T](x: T) -> Callable[..., T]:
    return lambda *args, **kwargs: x


Env = dict[str, str]
Command = list[str]


class Parameters(SimpleNamespace):
    def __or__(self, other: "Parameters") -> "Parameters":
        return Parameters(**vars(self), **vars(other))

    def __getitem__(self, name: str) -> Any:
        return getattr(self, name)

    @staticmethod
    def from_namespace(ns: argparse.Namespace) -> "Parameters":
        """
        Convert the argparse.Namespace to Parameters
        """
        return Parameters(**vars(ns))


@dataclass
class SuccessfulProcessResult:
    execution: "Execution"
    runtime: float
    stdout: str
    stderr: str
    rusage: Optional[resource.struct_rusage]


@dataclass
class FailedProcessResult:
    execution: "Execution"
    runtime: Optional[float]
    stdout: Optional[str]
    stderr: Optional[str]
    rusage: Optional[resource.struct_rusage]

    returncode: int
    # Only set for pre-execution failures (command not found, spawn OSError).
    # For process exits `returncode` is the single source of truth: 124 means
    # timed out (by convention of coreutils' `timeout(1)`), any other non-zero
    # value is a crash, and 0 is unreachable here.
    reason: Optional[str] = None

    @staticmethod
    def empty(execution: "Execution", reason: str) -> "FailedProcessResult":
        return FailedProcessResult(
            execution=execution,
            runtime=None,
            stdout=None,
            stderr=None,
            rusage=None,
            returncode=0,
            reason=reason,
        )


ProcessResult = SuccessfulProcessResult | FailedProcessResult


@dataclass
class Execution:
    """
    A definition of ready-to-run command
    """

    benchmark_name: str
    suite: str
    parser: Optional["ResultParser"]

    command: Command
    working_directory: Path
    env: Env

    timeout: Optional[float]

    info: dict[str, str]
    run: int = 1

    def as_identifier(self) -> str:
        ident = f"{self.suite},{self.benchmark_name}"

        parts = [f"{k}={v}" for k, v in self.info.items()]
        parts.append(f"run={self.run}")
        if parts:
            ident += " (" + ", ".join(parts) + ")"

        return ident

    @dataclass
    class Incomplete:
        """
        A intermediate state of Execution
        """

        benchmark_name: str
        data: tuple[Any, ...] | Any
        keys: SimpleNamespace

        suite: str
        parser: Optional["ResultParser"]

        command: Optional[Command]
        working_directory: Optional[Path]
        env: Env

        timeout: Optional[float]

        info: dict[str, str]
        run: int = 1

        def finalize(self) -> "Execution":
            if self.parser is None:
                raise ValueError(
                    f"Benchmark {self.benchmark_name} in suite {self.suite} is missing a parser"
                )

            if self.working_directory is None:
                raise ValueError(
                    f"Benchmark {self.benchmark_name} in suite {self.suite} is missing working directory"
                )

            if self.command is None:
                raise ValueError(
                    f"Benchmark {self.benchmark_name} in suite {self.suite} is missing a command"
                )

            return Execution(
                benchmark_name=self.benchmark_name,
                suite=self.suite,
                parser=self.parser,
                command=self.command,
                working_directory=self.working_directory,
                env=self.env,
                timeout=self.timeout,
                info=self.info,
                run=self.run,
            )


@dataclass
class Benchmark:
    """
    A definition of one benchmark. data and keys can be any benchmark-specific
    data that are needed for its execution.

    `data` is specified by positional arguments. If there is a single argument,
    `data` will not be a tuple but just that argument.

    `keys` are specified by keyword arguments
    """

    name: str
    data: tuple[Any, ...] | Any
    keys: SimpleNamespace

    def __init__(self, name: str, *data: Any, **keys: Any) -> None:
        self.name = name

        if len(data) == 1:
            self.data = data[0]
        else:
            self.data = data

        self.keys = SimpleNamespace(keys)

    @staticmethod
    def from_files(
        *paths: Path,
        recursive: bool = True,
        pattern: str | None = None,
    ) -> list[Benchmark]:
        """
        Create benchmarks from files and/or directories.

        For files, the benchmark name is the filename without extension.
        For directories, files are collected (recursively if *recursive* is
        True) and the benchmark name is the path relative to that directory
        without extension, e.g. for dir="tests" and file
        "tests/assignment/global.lox" the name will be "assignment/global".

        *pattern* is an optional regex matched against the filename
        (``re.search``); only matching files are included.
        """
        compiled = re.compile(pattern) if pattern is not None else None
        res: list[Benchmark] = []
        for p in paths:
            if p.is_dir():
                if recursive:
                    entries = (
                        dirpath / fname
                        for dirpath, _, fnames in p.walk()
                        for fname in fnames
                    )
                else:
                    entries = (child for child in p.iterdir() if child.is_file())
                for fp in entries:
                    if compiled is None or compiled.search(fp.name):
                        name = str(fp.relative_to(p).with_suffix(""))
                        res.append(Benchmark(name, path=fp))
            else:
                if compiled is None or compiled.search(p.name):
                    res.append(Benchmark(p.stem, path=p))
        return res
