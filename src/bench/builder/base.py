"""Builder foundation: the shared configuration base for the three builder levels.

`BuilderBase` declares every inheritable field once and carries the `with_*`
setters plus the `overlay` merge that cascades configuration across
`BenchAppBuilder` -> `SuiteBuilder` -> `BenchmarkBuilder` (defaults < app <
suite < benchmark: the more specific level wins). It also holds the small
primitives the builders share: the `Factory[T]` field-builder concept, the `UNSET`
sentinel, and the matrix/skip/env merge helpers.
"""

from __future__ import annotations

import dataclasses
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

from bench.builder.context import Context
from bench.core.invocation import SuccessFn
from bench.core.metric import (
    Metric,
)
from bench.core.outlier import OutlierDetection
from bench.core.policy import FixedRuns, StoppingPolicy

if TYPE_CHECKING:
    from _typeshed import StrOrBytesPath

    from bench.builder.benchmark import Benchmark
    from bench.runner.controller import Controller

# ----- Base types -------------------------
# TODO: Move to model
type UnresolvedCommand = Sequence[StrOrBytesPath]
type Env = Mapping[str, str]

# A label function turns a resolved benchmark into the human-readable variant
# identifier shown in reports Benchmark (not a Context) because labels reflect
# the resolved execution.
type LabelFn = Callable[[Benchmark], str]

# A skip predicate on a resolved `Benchmark`. Returning truthy drops the variant.
type SkipFn = Callable[[Benchmark], bool]

# ----- Builder types -------------------------

# A field builder: a `(ctx) -> value` resolved once per variant at create time
type Factory[T] = Callable[[Context[Any]], T]

type Timeout = float | None

# A matrix axis: either an explicit sequence of values or a factory.
# The factory sees limited context (params, suite and benchmark name)
type MatrixAxis = Sequence[Any] | Factory[Sequence[Any]]
# The normalized store form as either KV-pairs or unchanged factory
type MatrixAxisValues = tuple[Any, ...] | Factory[Sequence[Any]]

# ----- Builder helpers -------------------------


def const[T](value: T) -> Factory[T]:
    """Wrap a static value as a constant builder."""
    return lambda _: value


def merge_mapping[K, V](outer: Mapping[K, V], inner: Mapping[K, V]) -> Mapping[K, V]:
    return dict(outer) | dict(inner)


def merge_sequence[T](outer: Sequence[T], inner: Sequence[T]) -> Sequence[T]:
    return [*outer, *inner]


def merge_factory[T](
    value_merge: Callable[[T, T], T],
) -> Callable[[Factory[T], Factory[T]], Factory[T]]:
    def merge(outer: Factory[T], inner: Factory[T]) -> Factory[T]:
        return lambda ctx: value_merge(outer(ctx), inner(ctx))

    return merge


def as_build[T, U](
    value: T | Factory[U], normalize: Callable[[T], U] = lambda v: v
) -> Factory[U]:
    """Coerce a setter argument into a `Factory[U]`: a callable is the builder as
    is, anything else is the static value, normalized once and wrapped."""
    if callable(value):
        return cast("Factory[U]", value)
    return const(normalize(value))


def coerce_policy(p: StoppingPolicy | int) -> StoppingPolicy:
    """Accept the `int` shorthand for a stopping policy: `n` = FixedRuns(n)."""
    return p if isinstance(p, StoppingPolicy) else FixedRuns(p)


# ----- Matrix helpers -------------------------


def normalize_matrix(
    dims: Mapping[str, MatrixAxis],
) -> Mapping[str, MatrixAxisValues]:
    """Validate dimension names and freeze `{name: values}` into the canonical mapping."""
    # FIXME: Why?
    for name in dims:
        if name.startswith("_"):
            raise ValueError(f"Matrix dimension {name!r} cannot start with '_'")
    return {
        name: values if callable(values) else tuple(values)
        for name, values in dims.items()
    }


def merge_matrix(
    outer: Mapping[str, MatrixAxisValues], inner: Mapping[str, MatrixAxisValues]
) -> Mapping[str, MatrixAxisValues]:
    """Accumulate matrix dims for `overlay`: `inner` (more specific) dims first,
    then `outer`. A dimension declared on both sides is an error."""
    dup = inner.keys() & outer.keys()
    if dup:
        raise ValueError(f"Duplicate matrix axis '{next(iter(dup))!r}'")
    return dict(inner) | dict(outer)


@dataclass(frozen=True, slots=True)
class BuilderBase:
    """Shared configuration fields and `with_*` setters for the three builders
    (`BenchmarkBuilder`, `SuiteBuilder`, `BenchAppBuilder`).

    Every inheritable field is declared here once (defaulting to `UNSET`). The
    `with_*` setters each return a replaced copy typed as the concrete `Self`, and
    the `overlay` merge works uniformly across all three builders."""

    command: Factory[UnresolvedCommand] | None = None
    cwd: Factory[Path] | None = None
    env: Factory[Env] | None = None
    timeout: Factory[Timeout] | None = None
    metrics: Sequence[Factory[Metric]] = ()
    success: Factory[SuccessFn] | None = None
    warmup: Factory[StoppingPolicy] | None = None
    runs: Factory[StoppingPolicy] | None = None
    outlier_detection: OutlierDetection | None = None
    cooldown: float | None = None
    controller: Factory[Controller] | None = None
    label_fn: LabelFn | None = None
    matrix: Mapping[str, MatrixAxisValues] = dataclasses.field(
        default_factory=dict[str, MatrixAxisValues]
    )
    skips: Sequence[SkipFn] = ()

    # ----- helper function -------------------------

    def replace[T](
        self,
        field: str,
        value: T,
        *,
        override: bool,
        merge: Callable[[T, T], T] | None = None,
    ) -> Self:
        """
        Replace a field in the current builder. If override is `True`,
        always replace the previous value. Otherwise if `merge` is not `None`,
        merge the previous value with the new one. Otherwise print a warning and
        replace the value.
        """
        prev = getattr(self, field)
        if prev is not None:
            if not override:
                if merge is not None:
                    return dataclasses.replace(self, **{field: merge(prev, value)})
                else:
                    traceback.print_stack(file=sys.stdout)
                    print(
                        f"Warning: Overriding field {field}.\nTo avoid this warning, pass in `override=True`",
                        file=sys.stdout,
                    )

        return dataclasses.replace(self, **{field: value})

    # ----- command / environment / execution -------------------------

    def with_command(
        self,
        command: UnresolvedCommand | Factory[UnresolvedCommand],
        override: bool = False,
    ) -> Self:
        return self.replace(
            "command",
            as_build(command),
            override=override,
        )

    def with_cwd(
        self,
        cwd: str | Path | Factory[Path],
        override: bool = False,
    ) -> Self:
        return self.replace(
            "cwd",
            as_build(cwd, Path),
            override=override,
        )

    def with_env(self, env: Env | Factory[Env], override: bool = False) -> Self:
        return self.replace(
            "env",
            as_build(env, dict),
            override=override,
            merge=merge_factory(merge_mapping),
        )

    def with_timeout(
        self, timeout: Timeout | Factory[Timeout], override: bool = False
    ) -> Self:
        return self.replace(
            "timeout",
            as_build(timeout),
            override=override,
        )

    def with_controller(
        self, controller: Controller | Factory[Controller], override: bool = False
    ) -> Self:
        return self.replace(
            "controller",
            as_build(controller),
            override=override,
        )

    def with_success(self, fn: SuccessFn, override: bool = False) -> Self:
        return self.replace(
            "success",
            const(fn),
            override=override,
        )

    def with_success_factory(
        self, fn: Factory[SuccessFn], override: bool = False
    ) -> Self:
        return self.replace(
            "success",
            fn,
            override=override,
        )

    # ----- policies ---------------------------------------------------

    def with_warmup(
        self, p: int | StoppingPolicy | Factory[StoppingPolicy], override: bool = False
    ) -> Self:
        """Set the warmup policy."""
        return self.replace(
            "warmup",
            as_build(p, coerce_policy),
            override=override,
        )

    def with_runs(
        self, p: int | StoppingPolicy | Factory[StoppingPolicy], override: bool = False
    ) -> Self:
        """Set the policy for the measured runs."""
        return self.replace(
            "runs",
            as_build(p, coerce_policy),
            override=override,
        )

    def with_outlier_detection(
        self, d: OutlierDetection, override: bool = False
    ) -> Self:
        """Set the outlier-detection strategy (`NoDetection()` = off)."""
        return self.replace(
            "outlier_detection",
            d,
            override=override,
        )

    def with_cooldown(self, seconds: float, override: bool = False) -> Self:
        """Pause this long between successive process executions."""
        return self.replace(
            "cooldown",
            seconds,
            override=override,
        )

    # ----- matrix / skip / label --------------------------------------

    def with_matrix(self, **dims: MatrixAxis) -> Self:
        """Add matrix dimensions, merging with any already declared ones."""
        return self.replace(
            "matrix",
            normalize_matrix(dims),
            override=False,
            merge=merge_mapping,
        )

    def filter_benchmark(self, predicate: SkipFn) -> Self:
        return self.replace(
            "skips",
            [predicate],
            override=False,
            merge=merge_sequence,
        )

    def add_matrix_skip(self, /, **kwargs: Any) -> Self:
        """Drop variants: kwargs AND-matched against dims. Multiple calls compose as OR."""
        if len(kwargs) == 0:
            raise ValueError("At least one predicate should be defined")

        def rule(b: Benchmark) -> bool:
            for k, v in kwargs.items():
                if k not in b.data or b.data[k] != v:
                    return False

            return True

        return self.replace(
            "skips",
            [rule],
            override=False,
            merge=merge_sequence,
        )

    def with_label(self, fn: LabelFn, override: bool = False) -> Self:
        """Override how each variant's label renders in reports."""
        return self.replace(
            "label_fn",
            fn,
            override=override,
        )

    # ----- metrics ----------------------------------------------------

    def with_metric(
        self, *metrics: Metric | Factory[Metric], override: bool = False
    ) -> Self:
        """Set the per-iteration metrics, each reading stdout."""

        return self.replace(
            "metrics",
            [as_build(m) for m in metrics],
            override=override,
            merge=merge_sequence,
        )

    # ----- inheritance ------------------------------------------------

    def overlay[B: BuilderBase](self, over: B) -> B:
        """Merge `over` on top of `self` (over wins): the inheritance step used
        at every builder boundary (defaults < app < suite < benchmark).

        Each scalar/builder field takes `over`'s value if set, else `self`'s.
        `env` merges per key (over wins). `matrix` accumulates with `over`'s dims
        first (a name on both sides is an error). `skips` concatenate. Returns
        `over`'s type, so its own (non-shared) fields survive.
        """
        merged: dict[str, Any] = {}
        for name in _SHARED_FIELDS:
            if name in ("env", "matrix", "skips"):
                continue
            v = getattr(over, name)
            merged[name] = v if v is not None else getattr(self, name)

        if self.env is None:
            merged["env"] = over.env
        elif over.env is None:
            merged["env"] = self.env
        else:
            senv = self.env
            oenv = over.env

            def merge_env(ctx: Context[Any]) -> Env:
                return merge_mapping(senv(ctx), oenv(ctx))

            merged["env"] = merge_env

        merged["matrix"] = merge_matrix(self.matrix, over.matrix)
        merged["skips"] = (*self.skips, *over.skips)
        return dataclasses.replace(over, **merged)


_SHARED_FIELDS = tuple(f.name for f in dataclasses.fields(BuilderBase))
