from collections.abc import Callable
from typing import TYPE_CHECKING

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
