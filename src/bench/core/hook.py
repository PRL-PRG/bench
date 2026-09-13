from collections.abc import Callable
import os
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from bench.model.benchmark import Benchmark


class Hook:
    def setup(self, benchmark: Benchmark) -> None:
        pass

    def teardown(self, benchmark: Benchmark) -> None:
        pass


class SetupHook(Hook):
    __slots__ = ("_fun",)

    def __init__(self, fun: Callable[[Benchmark], None], /) -> None:
        self._fun = fun

    def setup(self, benchmark: Benchmark) -> None:
        self._fun(benchmark)


class TearDownHook(Hook):
    __slots__ = ("_fun",)

    def __init__(self, fun: Callable[[Benchmark], None], /) -> None:
        self._fun = fun

    def teardown(self, benchmark: Benchmark) -> None:
        self._fun(benchmark)


class PinCPUHook(Hook):
    __slots__ = ("_cpus",)

    def __init__(self, cpus: Iterable[int]) -> None:
        super().__init__()
        self._cpus = cpus

    def setup(self, benchmark: Benchmark) -> None:
        os.sched_setaffinity(0, self._cpus)
