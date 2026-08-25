"""`Context`: the single object passed to every builder callable, plus the
user-params-from-CLI glue that feeds it.

Users declare a `@dataclass` describing their parameters. `bench.run()`
auto-generates argparse arguments from the field annotations and constructs an
instance. That instance is exposed as `ctx.params` on the `Context` handed
to every command/cwd/env callable and suite factory, alongside the resolved
suite/benchmark properties (see `Context` below).

Supported param field types: `str`, `int`, `float`, `bool`, `Path`,
`Optional[T]` / `T | None`.

Required vs default:
  - field with no default              -> required argument
  - field with a default (or default_factory) -> optional, --help shows default
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from bench.params import Params

_MISSING_DEFAULT = object()


class Data:
    """A benchmark variant's payload: static data merged with its matrix-axis values."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] = {}) -> None:
        if "get" in data:
            raise ValueError('Cannot use the attribute key "get" in Data')

        self._data = data

    def __getattr__(self, name: str) -> Any:
        return self.get(name)

    def get(self, name: str, default: Any = _MISSING_DEFAULT) -> Any:
        res = object.__getattribute__(self, "_data").get(name, default)
        if res is _MISSING_DEFAULT:
            raise AttributeError(name)
        return res

    def __repr__(self) -> str:
        return f"Data({self._data!r})"

    @staticmethod
    def as_mapping(data: Data) -> Mapping[str, Any]:
        return data._data


@dataclass(frozen=True, slots=True)
class Context[T: Params]:
    """Context for the benchmark builder callable `with_*(lambda ctx: )` methods.

    `params` is the single object carrying every setting: the user's own fields
    plus, when their dataclass inherits `SharedBenchParams`/`SharedSelectionParams`,
    the builtin flags. When the user declares no params, `params` is a
    `SharedBenchParams` instance so the default pipeline still sees its flags.
    """

    params: T
    suite: str
    benchmark: str
    data: Data
