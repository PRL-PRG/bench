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

import argparse
import dataclasses
import types
import typing
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


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
class Cli:
    """The parsed bench runtime/selection flags, exposed on `Context.cli` so
    builder and reporter callables can branch on the invocation (verbose, dry,
    jobs, …) — the bench-owned counterpart to the user's `Context.params`."""

    verbose: bool = False
    dry: bool = False
    jobs: int = 1
    no_progress: bool = False
    json: str | None = None
    csv: str | None = None
    dir: str | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None
    list_plan: bool = False

    @classmethod
    def from_namespace(cls, ns: argparse.Namespace) -> Cli:
        """Pull the known flags off a parsed argparse namespace, ignoring any
        that aren't present (e.g. a subcommand that omits selection flags)."""
        present = {
            f.name: getattr(ns, f.name) for f in fields(cls) if hasattr(ns, f.name)
        }
        return cls(**present)


@dataclass(frozen=True, slots=True)
class Context[T]:
    """Context for the benchmark builder callable `with_*(lambda ctx: )` methods."""

    params: T
    suite: str
    benchmark: str | None
    matrix: Matrix
    cli: Cli = field(default_factory=Cli)  # parsed bench CLI flags


# Sentinel for "no value" used during dataclass instantiation when a field
# has a default. argparse's None default is fine for Optional fields.
_MISSING = object()


# TODO: should be private?
def add_dataclass_args(
    # argparse exposes no public name for the add_argument_group() return type.
    parser: argparse.ArgumentParser | argparse._ArgumentGroup,  # pyright: ignore[reportPrivateUsage]
    dc: type,
) -> None:
    """Generate `--<name>` arguments from a dataclass's fields."""
    if not is_dataclass(dc):
        raise TypeError(f"{dc!r} must be a @dataclass")
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
        return t, False  # ambiguous, argparse will treat as str

    origin = typing.get_origin(t)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(t) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return t, False


def _coerce_type(t: Any) -> Any:
    # Path is the only one not directly a callable that yields the right value
    # from a string, but Path(str) does, so it's fine.
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
