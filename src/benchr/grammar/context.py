"""`Context`: the single object passed to every builder callable, plus the
user-params-from-CLI glue that feeds it.

Users declare a `@dataclass` describing their parameters. `benchr.run()`
auto-generates argparse arguments from the field annotations and constructs an
instance. That instance is exposed as `ctx.params` on the `Context` handed
to every command/cwd/env callable and suite factory, alongside the resolved
suite/benchmark properties (see `Context` below).

Supported param field types: `str`, `int`, `float`, `bool`, `Path`,
`Optional[T]` / `T | None`.

Required vs default:
  - field with no default              → required argument
  - field with a default (or default_factory) → optional, --help shows default
"""

from __future__ import annotations

import argparse
import dataclasses
import types
import typing
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from benchr.core.execution import SuccessFn
    from benchr.core.metric import Metric
    from benchr.core.policy import StoppingPolicy


class Matrix:
    """A benchmark's variant/data payload"""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = dict(data or {})

    def __getattr__(self, name: str) -> Any:
        try:
            return object.__getattribute__(self, "_data")[name]
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:
        return f"Matrix({self._data!r})"


@dataclass(frozen=True, slots=True)
class Context[T]:
    """Everything a builder callable needs, in one object.

    The single argument passed to every command/cwd/env callable (built in
    `BenchmarkSpec._resolve_cell` when a variant is resolved) and to every
    suite factory (built in `Suite.materialize`). `T` is the user's params
    `@dataclass`.

    The level decides what the fields mean:

      - **suite level** (factories): `benchmark` is `None`, `matrix` is
        empty, and the policy/config fields are the *suite defaults* (already
        reflecting any `--runs/--warmup` CLI override).
      - **benchmark level** (command/cwd/env): `benchmark` is the name and the
        policy/config fields are the *resolved* benchmark's values.
    """

    params: T
    suite: str
    benchmark: str | None
    runs: StoppingPolicy
    warmup: StoppingPolicy
    timeout: float | None
    metrics: tuple[Metric, ...]
    harness: bool
    success: SuccessFn
    matrix: Matrix


# Sentinel for "no value" used during dataclass instantiation when a field
# has a default. argparse's None default is fine for Optional fields.
_MISSING = object()


def add_dataclass_args(
    # argparse exposes no public name for the add_argument_group() return type.
    parser: argparse.ArgumentParser | argparse._ArgumentGroup,  # pyright: ignore[reportPrivateUsage]
    dc: type,
) -> None:
    """Generate `--<name>` arguments from a dataclass's fields."""
    if not is_dataclass(dc):
        raise TypeError(f"{dc!r} must be a @dataclass")
    # Resolve string annotations (`from __future__ import annotations` makes
    # every `f.type` a string); fall back to the raw field types if the
    # forward refs can't be resolved.
    try:
        hints = typing.get_type_hints(dc)
    except Exception:
        hints = {}
    for f in fields(dc):
        flag = "--" + f.name.replace("_", "-")
        kwargs: dict[str, Any] = {"dest": f.name}
        bare_type, optional = _unwrap_optional(hints.get(f.name, f.type))
        is_bool = bare_type is bool
        if is_bool:
            kwargs["action"] = argparse.BooleanOptionalAction
        else:
            kwargs["type"] = _coerce_type(bare_type)
            kwargs["metavar"] = _metavar(bare_type)

        factory = f.default_factory
        has_default = (
            f.default is not dataclasses.MISSING or factory is not dataclasses.MISSING
        )
        if has_default:
            default: Any = f.default if factory is dataclasses.MISSING else factory()
            kwargs["default"] = default
            kwargs["help"] = f"(default: {default})"
        elif optional:
            kwargs["default"] = None
            kwargs["help"] = "(optional)"
        else:
            kwargs["required"] = True

        parser.add_argument(flag, **kwargs)


def build_dataclass(dc: type, namespace: argparse.Namespace) -> Any:
    """Instantiate the user dataclass from an argparse Namespace."""
    if not is_dataclass(dc):
        raise TypeError(f"{dc!r} must be a @dataclass")
    kwargs: dict[str, Any] = {}
    for f in fields(dc):
        val = getattr(namespace, f.name, _MISSING)
        if val is _MISSING:
            continue
        kwargs[f.name] = val
    return dc(**kwargs)


# ---------------------------------------------------------------------------
# Type plumbing
# ---------------------------------------------------------------------------


def _unwrap_optional(t: Any) -> tuple[Any, bool]:
    # Resolve string annotations if needed (from __future__ import annotations).
    if isinstance(t, str):
        return t, False  # ambiguous; argparse will treat as str

    origin = typing.get_origin(t)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(t) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return t, False


def _coerce_type(t: Any) -> Any:
    # Path is the only one not directly a callable that yields the right value
    # from a string — Path(str) does, so it's fine.
    if t is Path:
        return Path
    if t in (int, float, str):
        return t
    # Fallback: treat as a callable already
    return t if callable(t) else str


def _metavar(t: Any) -> str:
    if t is Path:
        return "PATH"
    if t is int:
        return "INT"
    if t is float:
        return "FLOAT"
    if t is bool:
        return ""
    return "STR"
