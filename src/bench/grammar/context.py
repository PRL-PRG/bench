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


class Data:
    """A benchmark variant's payload: static data merged with its matrix-axis values."""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = dict(data or {})

    def __getattr__(self, name: str) -> Any:
        try:
            return object.__getattribute__(self, "_data")[name]
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:
        return f"Data({self._data!r})"


@dataclass(frozen=True, slots=True)
class SharedSelectionParams:
    """The bench selection flags (`--include`/`--exclude`), reflected onto the
    CLI and exposed on `Context.cli` for `with_filter(...)` to consume."""

    include: list[str] | None = field(
        default=None,
        metadata={
            "metavar": "REGEX",
            "help": "Keep only benchmarks whose full name matches REGEX. "
            "Repeatable (OR semantics).",
        },
    )
    exclude: list[str] | None = field(
        default=None,
        metadata={
            "metavar": "REGEX",
            "help": "Drop benchmarks whose full name matches REGEX. "
            "Repeatable. Wins over --include.",
        },
    )


@dataclass(frozen=True, slots=True)
class SharedBenchParams(SharedSelectionParams):
    """The parsed bench runtime + selection flags, exposed on `Context.cli` so
    the `with_runner`/`with_reporter`/`with_filter` callables can branch on the
    invocation — the bench-owned counterpart to the user's `Context.params`."""

    jobs: int = field(
        default=1,
        metadata={
            "flags": ("-j",),
            "metavar": "N",
            "help": "Run up to N benchmarks in parallel (default: 1, sequential).",
        },
    )
    progress: bool = field(
        default=True,
        metadata={"help": "Suppress the progress bar with --no-progress."},
    )
    dry: bool = field(
        default=False,
        metadata={
            "action": "store_true",
            "help": "Show what shall happen but without running anything.",
        },
    )
    verbose: bool = field(
        default=False,
        metadata={"flags": ("-v",), "action": "store_true", "help": "Verbose output."},
    )
    json: str | None = field(
        default=None,
        metadata={
            "metavar": "FILE",
            "help": "Write a JSON report of every sample to FILE.",
        },
    )
    csv: str | None = field(
        default=None,
        metadata={
            "metavar": "FILE",
            "help": "Write a CSV report of every sample to FILE.",
        },
    )
    dir: str | None = field(
        default=None,
        metadata={
            "metavar": "DIR",
            "help": "Write a per-execution tree "
            "(stdout/stderr/exitcode/seq) under DIR.",
        },
    )


@dataclass(frozen=True, slots=True)
class Context[T]:
    """Context for the benchmark builder callable `with_*(lambda ctx: )` methods."""

    params: T
    suite: str
    benchmark: str | None
    data: Data
    cli: SharedBenchParams = field(default_factory=SharedBenchParams)


# Sentinel for "no value" used during dataclass instantiation when a field
# has a default. argparse's None default is fine for Optional fields.
_MISSING = object()


# TODO: should be private?
def add_dataclass_args(
    # argparse exposes no public name for the add_argument_group() return type.
    parser: argparse.ArgumentParser | argparse._ArgumentGroup,  # pyright: ignore[reportPrivateUsage]
    dc: type,
    *,
    skip: set[str] | None = None,
) -> None:
    """Generate `--<name>` arguments from a dataclass's fields.

    Per-field `field(metadata=...)` keys refine the generated argument:
      - `flags`: extra option strings, e.g. `("-j",)`.
      - `help`, `metavar`: verbatim overrides.
      - `action`: an argparse action override, e.g. `"store_true"`.
    A `list[T]` field becomes a repeatable `action="append"` argument. `skip`
    omits fields by name (used to split inherited fields across argument groups).
    """
    if not is_dataclass(dc):
        raise TypeError(f"{dc!r} must be a @dataclass")
    try:
        hints = typing.get_type_hints(dc)
    except Exception:
        hints = {}
    for f in fields(dc):
        if skip and f.name in skip:
            continue
        flags = ["--" + f.name.replace("_", "-"), *f.metadata.get("flags", ())]
        kwargs: dict[str, Any] = {"dest": f.name}
        bare_type, optional = _unwrap_optional(hints.get(f.name, f.type))
        action = f.metadata.get("action")
        if action:
            kwargs["action"] = action
        elif bare_type is bool:
            kwargs["action"] = argparse.BooleanOptionalAction
        elif typing.get_origin(bare_type) is list:
            elem = typing.get_args(bare_type)[0]
            kwargs["action"] = "append"
            kwargs["type"] = _coerce_type(elem)
            kwargs["metavar"] = f.metadata.get("metavar", _metavar(elem))
        else:
            kwargs["type"] = _coerce_type(bare_type)
            kwargs["metavar"] = f.metadata.get("metavar", _metavar(bare_type))

        factory = f.default_factory
        has_default = (
            f.default is not dataclasses.MISSING or factory is not dataclasses.MISSING
        )
        if has_default:
            default: Any = f.default if factory is dataclasses.MISSING else factory()
            kwargs["default"] = default
            kwargs["help"] = f.metadata.get("help", f"(default: {default})")
        elif optional:
            kwargs["default"] = None
            kwargs["help"] = f.metadata.get("help", "(optional)")
        else:
            kwargs["required"] = True
            if "help" in f.metadata:
                kwargs["help"] = f.metadata["help"]

        parser.add_argument(*flags, **kwargs)


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
