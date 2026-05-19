"""RunContext: user-defined params materialized from CLI flags.

Users declare a ``@dataclass`` describing their parameters. ``benchr.run()``
auto-generates argparse arguments from the field annotations and constructs
an instance which is passed to every builder lambda as ``ctx``.

Supported field types: ``str``, ``int``, ``float``, ``bool``, ``Path``,
``Optional[T]`` / ``T | None``.

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
from typing import Any


# Sentinel for "no value" used during dataclass instantiation when a field
# has a default. argparse's None default is fine for Optional fields.
_MISSING = object()


def add_dataclass_args(
    parser: argparse.ArgumentParser | argparse._ArgumentGroup,
    dc: type,
) -> None:
    """Generate ``--<name>`` arguments from a dataclass's fields."""
    if not is_dataclass(dc):
        raise TypeError(f"{dc!r} must be a @dataclass")
    for f in fields(dc):
        flag = "--" + f.name.replace("_", "-")
        kwargs: dict[str, Any] = {"dest": f.name}
        bare_type, optional = _unwrap_optional(f.type)
        is_bool = bare_type is bool
        if is_bool:
            kwargs["action"] = argparse.BooleanOptionalAction
        else:
            kwargs["type"] = _coerce_type(bare_type)
            kwargs["metavar"] = _metavar(bare_type)

        has_default = (
            f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        )
        if has_default:
            default = (
                f.default
                if f.default is not dataclasses.MISSING
                else f.default_factory()  # type: ignore[misc]
            )
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
    """Return (inner_type, is_optional). Handles ``T | None`` and ``Optional[T]``.

    Also resolves PEP 604 unions of just ``T | None``.
    """
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
    """Return a callable that argparse can use as ``type=``."""
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
