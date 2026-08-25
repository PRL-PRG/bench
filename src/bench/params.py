from __future__ import annotations

import argparse
import dataclasses
import types
import typing
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, dataclass_transform


@dataclass_transform(
    frozen_default=True,
    kw_only_default=True,
    field_specifiers=(field,),
)
class ParamsMeta(type):
    """Makes every `Params` subclass a frozen, slotted, keyword-only dataclass.

    A user declaring params writes a plain class body of annotated fields; the
    `@dataclass(...)` call and its options live here, so every params class is
    uniform. `dataclass_transform` tells type checkers the same thing, so the
    synthesized `__init__` and the field types are checked as usual.
    """

    def __new__(
        mcls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        /,
        **kwargs: Any,
    ):
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)
        if namespace.get("_params_dataclass_applied"):
            return cls
        setattr(cls, "_params_dataclass_applied", True)
        return dataclass(frozen=True, kw_only=True)(cls)


class Params(metaclass=ParamsMeta):
    """Base for a user's params class: subclass it and declare annotated fields.

    Needs no `@dataclass` decorator - `ParamsMeta` applies it, frozen, slotted
    and keyword-only."""

    def __post_init__(self) -> None:
        for f in fields(self):
            for k in f.metadata:
                if not isinstance(k, str):
                    raise ValueError(
                        f"Params metadata should strings, got {type(k)} for key {k}"
                    )

    if TYPE_CHECKING:
        # Any attribute access is "ok" from the view of typechecker
        def __getattr__(self, name: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class ParamsGroup:
    title: str
    description: str | None = None


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

SELECTION_GROUP = ParamsGroup("selection")


class SharedSelectionParams(Params):
    """The bench selection flags (`--include`/`--exclude`). A user's params
    dataclass inherits this to opt into `--include`/`--exclude` on the CLI and
    have the default `with_filter(...)` honor them."""

    include: list[str] | None = field(
        default=None,
        metadata={
            "group": SELECTION_GROUP,
            "metavar": "REGEX",
            "help": "Keep only benchmarks whose full name matches REGEX. "
            "Repeatable (OR semantics).",
        },
    )
    exclude: list[str] | None = field(
        default=None,
        metadata={
            "group": SELECTION_GROUP,
            "metavar": "REGEX",
            "help": "Drop benchmarks whose full name matches REGEX. "
            "Repeatable. Wins over --include.",
        },
    )


RUNNER_GROUP = ParamsGroup("runner")


class SharedRunnerParams(Params):
    jobs: int = field(
        default=1,
        metadata={
            "group": RUNNER_GROUP,
            "flags": ("-j",),
            "metavar": "N",
            "help": "Run up to N benchmarks in parallel (default: 1, sequential).",
        },
    )

    dry: bool = field(
        default=False,
        metadata={
            "group": RUNNER_GROUP,
            "action": "store_true",
            "help": "Show what shall happen but without running anything.",
        },
    )

    verbose: bool = field(
        default=False,
        metadata={
            "group": RUNNER_GROUP,
            "flags": ("-v",),
            "action": "store_true",
            "help": "Verbose output.",
        },
    )


REPORTER_GROUP = ParamsGroup("reporter")


class SharedReporterParams(Params):
    """The bench runtime + selection flags. A user's params dataclass inherits
    this to opt into the full builtin flag set (`-j`/`--progress`/`--json`/...
    plus `--include`/`--exclude`) and have the default runner/reporter/filter
    honor them. When a user declares no params, this is the effective params
    type, so the builtin flags are always available out of the box."""

    progress: bool = field(
        default=True,
        metadata={
            "group": REPORTER_GROUP,
            "help": "Suppress the progress bar with --no-progress.",
        },
    )

    json: str | None = field(
        default=None,
        metadata={
            "group": REPORTER_GROUP,
            "metavar": "FILE",
            "help": "Write a JSON report of every sample to FILE.",
        },
    )

    csv: str | None = field(
        default=None,
        metadata={
            "group": REPORTER_GROUP,
            "metavar": "FILE",
            "help": "Write a CSV report of every sample to FILE.",
        },
    )

    dir: str | None = field(
        default=None,
        metadata={
            "group": REPORTER_GROUP,
            "metavar": "DIR",
            "help": "Write a per-execution tree (stdout/stderr/exitcode/seq) under DIR.",
        },
    )


class SharedBenchParams(
    SharedSelectionParams, SharedRunnerParams, SharedReporterParams
):
    pass


# ---------------------------------------------------------------------------
# Argparse adaptor
# ---------------------------------------------------------------------------


type _Flags = list[str]
type _Kwargs = dict[str, Any]
type _Arg = tuple[_Flags, _Kwargs]


# TODO: Remove skip
def add_dataclass_args(
    # argparse exposes no public name for the add_argument_group() return type.
    parser: argparse.ArgumentParser | argparse._ArgumentGroup,  # pyright: ignore[reportPrivateUsage]
    dc: type,
    *,
    skip: set[str] = set(),
) -> None:
    """Generate `--<name>` arguments from a dataclass's fields.

    Per-field `field(metadata=...)` keys refine the generated argument:
      - `flags`: extra option strings, e.g. `("-j",)`.
      - `group`: a group to group this under
      - any other keyword: other arguments to argparse `add_argument`
    A `list[T]` field becomes a repeatable `action="append"` argument. `skip`
    omits fields by name (used to split inherited fields across argument groups).
    """
    if not is_dataclass(dc):
        raise TypeError(f"{dc!r} must be a @dataclass")

    try:
        hints = typing.get_type_hints(dc)
    except Exception:
        hints = {}

    grouped: dict[ParamsGroup | None, list[_Arg]] = dict()

    for f in fields(dc):
        if f.name in skip:
            continue

        kwargs = cast(_Kwargs, dict(f.metadata))
        flags = ["--" + f.name.replace("_", "-"), *kwargs.pop("flags", ())]

        typ = hints.get(f.name, f.type)
        bare_type, optional = _unwrap_optional(typ)

        # Set based on type
        if bare_type is bool:
            kwargs.setdefault("action", argparse.BooleanOptionalAction)
        elif typing.get_origin(bare_type) is list:
            elem = typing.get_args(bare_type)[0]
            kwargs.setdefault("action", "append")
            kwargs.setdefault("type", _coerce_type(elem))
            kwargs.setdefault("metavar", _metavar(elem))
        else:
            kwargs.setdefault("type", _coerce_type(bare_type))
            kwargs.setdefault("metavar", _metavar(bare_type))

        # Set help
        if "help" not in kwargs:
            kwargs["help"] = ""

        # Set default
        has_default = True
        if "default" in kwargs:
            default = kwargs["default"]
        elif f.default is not dataclasses.MISSING:
            default = f.default
        elif f.default_factory is not dataclasses.MISSING:
            default = f.default_factory()
        else:
            has_default = False
            default = None

        if has_default:
            kwargs["default"] = default
            kwargs["help"] += f" (default: {default})"
        elif optional:
            kwargs["default"] = None
            kwargs["help"] += " (optional)"
        else:
            kwargs.setdefault("required", True)

        group = kwargs.pop("group", None)
        grouped.setdefault(group, list()).append(
            (flags, kwargs)
        )

    for group, args in grouped.items():
        if group is None:
            p = parser
        else:
            p = parser.add_argument_group(
                title=group.title,
                description=group.description,
            )

        for flags, kwargs in args:
            p.add_argument(*flags, **kwargs)


def build_dataclass[T: Params](dc: type[T], namespace: argparse.Namespace) -> T:
    """Instantiate the user dataclass from an argparse Namespace."""
    names = set(f.name for f in fields(dc))
    args = {k: v for k, v in vars(namespace).items() if k in names}
    return dc(**args)


# ---------------------------------------------------------------------------
# Type plumbing
# ---------------------------------------------------------------------------


def _unwrap_optional(t: Any | str | None) -> tuple[Any, bool]:
    if isinstance(t, str):
        return t, False

    origin = typing.get_origin(t)
    optional = False

    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(t)

        non_opt_args = [a for a in args if a is not type(None)]
        if len(non_opt_args) != len(args):
            optional = True

        if len(non_opt_args) == 1:
            return non_opt_args[0], optional

    return t, optional


def _coerce_type(t: Any) -> Any | None:
    if t in (int, float, str):
        return t
    # Fallback: treat as a callable already
    return t if callable(t) else None


def _metavar(t: Any) -> str | None:
    if t is Path:
        return "PATH"
    if t is int:
        return "INT"
    if t is float:
        return "FLOAT"
    if t is bool:
        return ""
    return None
