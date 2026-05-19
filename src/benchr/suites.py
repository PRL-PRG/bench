import abc
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence, TYPE_CHECKING

from benchr.types import const, Env, Command, Parameters, Execution, Benchmark

if TYPE_CHECKING:
    from benchr.parsers import ResultParser


class BenchmarkCollection[This](abc.ABC):
    """
    Abstract superclass of Suite and Benchmark - implementation detail
    """

    @abc.abstractmethod
    def apply_suite_decorator(self, decorator: Callable[["Suite"], "Suite"]) -> This:
        """
        Apply a decorator to all Suites inside this collection
        """
        ...

    def matrix[T](
        self,
        matrix: "Matrix[T]",
    ) -> This:
        """
        Add a matrix parameter. Each benchmark is going to be duplicated with
        each instance having one value from the matrix's parameters.

        The matrix's builder methods (command, working_directory, env, info)
        control how each variant is constructed.
        """
        return self.apply_suite_decorator(matrix.build)

    def runs(self, value: int) -> This:
        """
        Run each benchmark `value` times without any other modification
        """
        return self.apply_suite_decorator(lambda suite: RunsSuite(suite, value))

    def timeout(self, timeout: float) -> This:
        """
        Set the timeout of benchmarks (in seconds)
        """
        return self.apply_suite_decorator(
            lambda suite: TimeoutSuite(
                suite,
                timeout,
            )
        )


class Suite(BenchmarkCollection["Suite"]):
    """
    A collection of benchmarks
    """

    def apply_suite_decorator(self, decorator: Callable[["Suite"], "Suite"]) -> "Suite":
        return decorator(self)

    @abc.abstractmethod
    def get_executions(
        self, parameters: Parameters
    ) -> Iterator[Execution.Incomplete]: ...

    def to_config(self) -> "Config":
        """
        Create a simplified config with only one Suite
        """
        return Config([self])


class BaseSuite(Suite):
    name: str
    benchmarks: Callable[[Parameters], list[Benchmark]]

    command: Optional[Callable[[Parameters, Benchmark], Command]]
    working_directory: Optional[Callable[[Parameters, Benchmark], Path]]
    env: Callable[[Parameters, Benchmark], Env]

    parser: Optional["ResultParser"]

    def __init__(
        self,
        name: str,
        benchmarks: Callable[[Parameters], list[Benchmark]],
        command: Optional[Callable[[Parameters, Benchmark], Command]],
        working_directory: Optional[Callable[[Parameters, Benchmark], Path]],
        env: Callable[[Parameters, Benchmark], Env],
        parser: Optional["ResultParser"],
    ) -> None:
        self.name = name
        self.benchmarks = benchmarks

        self.command = command
        self.working_directory = working_directory
        self.env = env

        self.parser = parser

    def get_executions(self, parameters: Parameters) -> Iterator[Execution.Incomplete]:
        benchs = self.benchmarks(parameters)

        if len(benchs) == 0:
            raise ValueError(f"No benchmarks defined in {self.name}!")

        for b in benchs:
            if self.command is not None:
                command = self.command(parameters, b)
            else:
                command = None

            if self.working_directory is not None:
                working_directory = self.working_directory(parameters, b)
            else:
                working_directory = None

            env = self.env(parameters, b)

            yield Execution.Incomplete(
                benchmark_name=b.name,
                data=b.data,
                keys=b.keys,
                suite=self.name,
                command=command,
                parser=self.parser,
                working_directory=working_directory,
                env=env,
                timeout=None,
                info={},
            )


def suite(
    name: str,
    benchmarks: Sequence[Benchmark | str] | Callable[[Parameters], list[Benchmark]],
    *,
    command: Optional[Callable[[Parameters, Benchmark], Command]] = None,
    working_directory: Optional[Callable[[Parameters, Benchmark], Path] | Path] = None,
    env: Callable[[Parameters, Benchmark], Env] | Env = {},
    parser: Optional["ResultParser"] = None,
) -> Suite:
    """
    Flexible way of constructing a Suite
    """
    if not callable(benchmarks):
        benchmarks = const(
            [Benchmark(b) if isinstance(b, str) else b for b in benchmarks]
        )

    if working_directory is not None and not callable(working_directory):
        working_directory = const(working_directory)

    if not callable(env):
        env = const(env)

    return BaseSuite(
        name=name,
        benchmarks=benchmarks,
        command=command,
        working_directory=working_directory,
        env=env,
        parser=parser,
    )


class SuiteDecorator(Suite):
    """
    A Suite that extends another Suite
    """

    parent: Suite

    def __init__(self, parent: Suite) -> None:
        self.parent = parent

    def get_executions(self, parameters: Parameters) -> Iterator[Execution.Incomplete]:
        for pexe in self.parent.get_executions(parameters):
            for exe in self.extend_execution(parameters, pexe):
                yield exe

    @abc.abstractmethod
    def extend_execution(
        self, parameters: Parameters, execution: Execution.Incomplete
    ) -> Iterator[Execution.Incomplete]:
        """
        This method needs to be implemented, as it is the one that extends the
        parent suite
        """
        ...


type MatrixCallable[T, R] = Callable[[Parameters, Execution.Incomplete, T], R]


class MatrixSuite[T](SuiteDecorator):
    name: str
    parameters: Sequence[T]

    matrix_command: Optional[MatrixCallable[T, Command]]
    matrix_working_directory: Optional[MatrixCallable[T, Path]]
    matrix_env: MatrixCallable[T, Env]
    matrix_info: Optional[Callable[[T], dict[str, str]]]

    def __init__(
        self,
        name: str,
        parent: Suite,
        parameters: Sequence[T],
        matrix_command: Optional[MatrixCallable[T, Command]],
        matrix_working_directory: Optional[MatrixCallable[T, Path]],
        matrix_env: MatrixCallable[T, Env],
        matrix_info: Optional[Callable[[T], dict[str, str]]],
    ) -> None:
        super().__init__(parent)

        self.name = name
        self.parameters = parameters

        self.matrix_command = matrix_command
        self.matrix_working_directory = matrix_working_directory
        self.matrix_env = matrix_env
        self.matrix_info = matrix_info

    def extend_execution(
        self, parameters: Parameters, execution: Execution.Incomplete
    ) -> Iterator[Execution.Incomplete]:
        for p in self.parameters:
            if self.matrix_command is not None:
                c = self.matrix_command(parameters, execution, p)
            else:
                c = execution.command

            e = execution.env | self.matrix_env(parameters, execution, p)

            if self.matrix_working_directory is not None:
                wd = self.matrix_working_directory(parameters, execution, p)
            else:
                wd = execution.working_directory

            if self.matrix_info is not None:
                i = execution.info | self.matrix_info(p)
            else:
                i = execution.info | {self.name: str(p)}

            yield dataclasses.replace(
                execution,
                command=c,
                env=e,
                working_directory=wd,
                info=i,
            )


class RunsSuite(SuiteDecorator):
    count: int

    def __init__(self, parent: Suite, count: int) -> None:
        super().__init__(parent)
        self.count = count

    def extend_execution(
        self, parameters: Parameters, execution: Execution.Incomplete
    ) -> Iterator[Execution.Incomplete]:
        for i in range(1, self.count + 1):
            yield dataclasses.replace(execution, run=i)


@dataclass
class Matrix[T]:
    """
    The MatrixSuite builder
    """

    name: str
    parameters: Sequence[T]

    matrix_command: Optional[MatrixCallable[T, Command]] = None
    matrix_working_directory: Optional[MatrixCallable[T, Path]] = None
    matrix_env: MatrixCallable[T, Env] = const({})
    matrix_info: Optional[Callable[[T], dict[str, str]]] = None

    def __init__(
        self,
        name: str,
        parameters: Sequence[T],
    ) -> None:
        self.name = name
        self.parameters = parameters

    def command(self, callback: Callable[[T], Command]):
        return self.command_full(lambda ps, ex, p: callback(p))

    def command_full(self, callback: MatrixCallable[T, Command]):
        if self.matrix_command is not None:
            raise ValueError("Multiple definitions of command")

        return dataclasses.replace(self, matrix_command=callback)

    def working_directory(self, callback: Callable[[T], Path]):
        return self.working_directory_full(lambda ps, ex, p: callback(p))

    def working_directory_full(self, callback: MatrixCallable[T, Path]):
        if self.matrix_working_directory is not None:
            raise ValueError("Multiple definitions of working directory")

        return dataclasses.replace(self, matrix_working_directory=callback)

    def env(self, name: Optional[str]):
        if name is None:
            name = self.name

        return self.env_callback_full(lambda ps, ex, p: {name: str(p)})

    def env_callback(self, callback: Callable[[T], Env]):
        return self.env_callback_full(lambda ps, ex, p: callback(p))

    def env_callback_full(self, callback: MatrixCallable[T, Env]):
        prev_mk_env = self.matrix_env
        mk_env = lambda ps, ex, p: prev_mk_env(ps, ex, p) | callback(ps, ex, p)

        return dataclasses.replace(self, matrix_env=mk_env)

    def info(self, callback: Callable[[T], dict[str, str]]):
        if self.matrix_info is not None:
            raise ValueError("Multiple definitions of info")

        return dataclasses.replace(self, matrix_info=callback)

    def build(self, suite: Suite) -> MatrixSuite:
        return MatrixSuite(
            name=self.name,
            parent=suite,
            parameters=self.parameters,
            matrix_command=self.matrix_command,
            matrix_working_directory=self.matrix_working_directory,
            matrix_env=self.matrix_env,
            matrix_info=self.matrix_info,
        )


class TimeoutSuite(SuiteDecorator):
    timeout_value: float

    def __init__(
        self,
        parent: Suite,
        timeout_value: float,
    ) -> None:
        super().__init__(parent)
        self.timeout_value = timeout_value

    def extend_execution(
        self, parameters: Parameters, execution: Execution.Incomplete
    ) -> Iterator[Execution.Incomplete]:
        yield dataclasses.replace(execution, timeout=self.timeout_value)


@dataclass
class Config(BenchmarkCollection["Config"]):
    """
    The full configuration of all suites.
    """

    suites: list[Suite]

    default_parser: Optional["ResultParser"] = None
    default_command: Optional[Callable[[Parameters, Execution.Incomplete], Command]] = (
        None
    )
    default_working_directory: Optional[
        Callable[[Parameters, Execution.Incomplete], Path]
    ] = None
    default_env: Callable[[Parameters, Execution.Incomplete], Env] = const({})

    def parser(self, default_parser: "ResultParser") -> "Config":
        if self.default_parser is not None:
            raise ValueError("Multiple definitions of default parser")

        return dataclasses.replace(self, default_parser=default_parser)

    def command(
        self,
        default_command: Callable[[Parameters, Execution.Incomplete], Command]
        | Command,
    ) -> "Config":
        """
        Define a default command for all benchmarks
        """
        if self.default_command is not None:
            raise ValueError("Multiple definitions of default command")

        if not callable(default_command):
            default_command = const(default_command)

        return dataclasses.replace(self, default_command=default_command)

    def working_directory(
        self,
        default_working_directory: Callable[[Parameters, Execution.Incomplete], Path]
        | Path,
    ) -> "Config":
        """
        Define a default working directory for all benchmarks
        """
        if self.default_working_directory is not None:
            raise ValueError("Multiple definitions of default working directory")

        if not callable(default_working_directory):
            default_working_directory = const(default_working_directory)

        return dataclasses.replace(
            self, default_working_directory=default_working_directory
        )

    def env(
        self,
        default_env: Callable[[Parameters, Execution.Incomplete], Env] | Env,
    ) -> "Config":
        """
        Define a default environment for all benchmarks
        """
        if not callable(default_env):
            default_env = const(default_env)

        prev_default_env = self.default_env
        callback = lambda ps, e: prev_default_env(ps, e) | default_env(ps, e)

        return dataclasses.replace(self, default_env=callback)

    def get_executions(self, parameters: Parameters) -> list[Execution]:
        """
        Return all executions for this configuration
        """
        res = []
        for suite in self.suites:
            for exe in suite.get_executions(parameters):
                if exe.parser is None:
                    if self.default_parser is None:
                        raise ValueError(
                            f"No result parser for benchmark {exe.benchmark_name} in suite {exe.suite}"
                        )

                    exe.parser = self.default_parser

                if exe.command is None:
                    if self.default_command is None:
                        raise ValueError(
                            f"No command for benchmark {exe.benchmark_name} in suite {exe.suite}"
                        )

                    exe.command = self.default_command(parameters, exe)

                if exe.working_directory is None:
                    if self.default_working_directory is None:
                        raise ValueError(
                            f"No working directory for benchmark {exe.benchmark_name} in suite {exe.suite}"
                        )

                    exe.working_directory = self.default_working_directory(
                        parameters, exe
                    )

                exe.env = self.default_env(parameters, exe) | exe.env

                res.append(exe.finalize())
        return res

    def apply_suite_decorator(
        self, decorator: Callable[["Suite"], "Suite"]
    ) -> "Config":
        return dataclasses.replace(self, suites=list(map(decorator, self.suites)))
