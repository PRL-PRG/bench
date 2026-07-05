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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Self, cast

from bench.core.execution import EMPTY_MAPPING, SuccessFn, to_argv
from bench.core.metric import (
    IterationMetric,
    MetricSource,
    ProcessMetric,
    StdoutMetricSource,
    as_metric_source,
)
from bench.core.outlier import OutlierDetection
from bench.core.policy import StoppingPolicy, coerce_policy
from bench.grammar.context import Context

if TYPE_CHECKING:
    from _typeshed import StrOrBytesPath

    from bench.grammar.benchmark import Benchmark
    from bench.runner.source import HarnessMonitor

# A field builder: a `(ctx) -> value` resolved once per variant at create time
type Factory[T] = Callable[[Context[Any]], T]

type CommandFactory = Factory[Sequence[StrOrBytesPath]]
type PathFactory = Factory[Path]
type EnvFactory = Factory[Mapping[str, str]]

# A matrix axis: either an explicit sequence of values or a factory.
# The factory sees limited context (params, suite and benchmark name)
type MatrixAxis = Sequence[Any] | Factory[Sequence[Any]]
# The normalized store form as either KV-pairs or unchanged factory
type MatrixAxisValues = tuple[Any, ...] | Factory[Sequence[Any]]

# A label function turns a resolved benchmark into the human-readable variant
# identifier shown in reports Benchmark (not a Context) because labels reflect
# the resolved execution.
type LabelFn = Callable[[Benchmark], str]

# A skip predicate on a resolved `Benchmark`. Returning truthy drops the variant.
type SkipFn = Callable[[Benchmark], bool]

# A harness monitor, built once per variant (parallels Factory[T] for every
# other field) rather than reused as-is - see with_harness/with_monitor_fn.
type HarnessMonitorFactory = Factory[HarnessMonitor | None]

type Command = StrOrBytesPath | Sequence[StrOrBytesPath] | CommandFactory


_UNSET_MSG = "benchmark field is unset"


class _Unset:
    __slots__ = ()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(_UNSET_MSG)

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(_UNSET_MSG)

    def __bool__(self) -> bool:
        raise RuntimeError(_UNSET_MSG)

    def __repr__(self) -> str:
        return "UNSET"


# Typed `Any` so fields keep their concrete declared types. Misuse of an
# unresolved factory fails loudly at runtime in one place (above).
UNSET: Any = _Unset()


def const(value: Any) -> Factory[Any]:
    """Wrap a static value as a constant builder."""
    return lambda _ctx: value


def as_build(value: Any, normalize: Callable[[Any], Any] = lambda v: v) -> Factory[Any]:
    """Coerce a setter argument into a `Factory[T]`: a callable is the builder as
    is, anything else is the static value, normalized once and wrapped."""
    if callable(value):
        return cast("Factory[Any]", value)
    return const(normalize(value))


def normalize_matrix(
    dims: Mapping[str, MatrixAxis],
) -> Mapping[str, MatrixAxisValues]:
    """Validate dimension names and freeze `{name: values}` into the canonical mapping."""
    for name in dims:
        if name.startswith("_"):
            raise ValueError(f"Matrix dimension {name!r} cannot start with '_'")
    return MappingProxyType(
        {
            name: values if callable(values) else tuple(values)
            for name, values in dims.items()
        }
    )


def make_skip_rule(
    predicate: SkipFn | None, kwargs: Mapping[str, Any]
) -> SkipFn | None:
    """Build a skip predicate from a predicate and/or AND-matched kwargs. `None`
    when neither is given (the caller then leaves its skip list untouched).

    A variant is dropped when the returned predicate is truthy. Within one rule
    all kwargs must match AND the predicate (if any) must return truthy.
    """
    if predicate is None and not kwargs:
        return None
    return lambda b: (
        all(hasattr(b, k) and getattr(b, k) == v for k, v in kwargs.items())
        and (predicate is None or predicate(b))
    )


def _merge_env(base: EnvFactory, over: EnvFactory) -> EnvFactory:
    """Lazy per-key env merge for `overlay`: `base` first, `over` wins. `UNSET`
    on either side contributes nothing. Both unset stays `UNSET`."""
    if base is UNSET:
        return over
    if over is UNSET:
        return base
    return lambda ctx: {**base(ctx), **over(ctx)}


def _merge_matrix(
    outer: Mapping[str, MatrixAxisValues], inner: Mapping[str, MatrixAxisValues]
) -> Mapping[str, MatrixAxisValues]:
    """Accumulate matrix dims for `overlay`: `inner` (more specific) dims first,
    then `outer`. A dimension declared on both sides is an error."""
    dup = inner.keys() & outer.keys()
    if dup:
        raise ValueError(f"matrix dimension {next(iter(dup))!r} already declared")
    return MappingProxyType({**inner, **outer})


def _raise_builder_type_error(
    field: str, expected_type: str, value: object, hint: str = ""
) -> NoReturn:
    """Uniform TypeError for a builder setter given a wrong-typed argument."""
    msg = f"{field} expects {expected_type}, got {type(value).__name__}"
    if hint:
        msg += f"; {hint}"
    raise TypeError(msg)


@dataclass(frozen=True, slots=True)
class BuilderBase:
    """Shared configuration fields and `with_*` setters for the three builders
    (`BenchmarkBuilder`, `SuiteBuilder`, `BenchAppBuilder`).

    Every inheritable field is declared here once (defaulting to `UNSET`), so the
    setters, each returning a replaced copy with the concrete `Self` type.
    The `overlay` merge work uniformly on all three."""

    command: CommandFactory = UNSET
    cwd: PathFactory = UNSET
    env: EnvFactory = UNSET
    timeout: Factory[float | None] = UNSET
    iteration_metrics: Factory[tuple[tuple[IterationMetric, MetricSource], ...]] = UNSET
    process_metrics: Factory[tuple[ProcessMetric, ...]] = UNSET
    success: Factory[SuccessFn] = UNSET
    warmup: Factory[StoppingPolicy] = UNSET
    runs: Factory[StoppingPolicy] = UNSET
    outlier_detection: OutlierDetection = UNSET
    cooldown: float = UNSET
    label_fn: LabelFn = UNSET
    harness: bool = UNSET
    monitor: HarnessMonitorFactory | None = UNSET
    matrix: Mapping[str, MatrixAxisValues] = EMPTY_MAPPING
    skips: tuple[SkipFn, ...] = ()

    # ----- command / environment / execution -------------------------

    def with_command(self, command: Command) -> Self:
        return dataclasses.replace(self, command=as_build(command, to_argv))

    def with_cwd(self, cwd: str | Path | PathFactory) -> Self:
        return dataclasses.replace(self, cwd=as_build(cwd, Path))

    def with_env(self, env: Mapping[str, str] | EnvFactory) -> Self:
        return dataclasses.replace(self, env=as_build(env, dict))

    def with_timeout(self, timeout: float | None | Factory[float | None]) -> Self:
        return dataclasses.replace(self, timeout=as_build(timeout))

    def with_success(self, fn: SuccessFn) -> Self:
        return dataclasses.replace(self, success=const(fn))

    def with_success_fn(self, fn: Factory[SuccessFn]) -> Self:
        return dataclasses.replace(self, success=fn)

    # ----- policies ---------------------------------------------------

    def with_warmup(self, p: int | StoppingPolicy | Factory[StoppingPolicy]) -> Self:
        """Set the warmup policy."""
        return dataclasses.replace(self, warmup=as_build(p, coerce_policy))

    def with_runs(self, p: int | StoppingPolicy | Factory[StoppingPolicy]) -> Self:
        """Set the policy for the measured runs."""
        return dataclasses.replace(self, runs=as_build(p, coerce_policy))

    def with_outlier_detection(self, d: OutlierDetection) -> Self:
        """Set the outlier-detection strategy (`NoDetection()` = off)."""
        return dataclasses.replace(self, outlier_detection=d)

    def with_cooldown(self, seconds: float) -> Self:
        """Pause this long between successive process executions."""
        return dataclasses.replace(self, cooldown=seconds)

    # ----- matrix / skip / label --------------------------------------

    def with_matrix(self, **dims: MatrixAxis) -> Self:
        """Declare matrix dimensions."""
        return dataclasses.replace(self, matrix=normalize_matrix(dims))

    def add_matrix(self, **dims: MatrixAxis) -> Self:
        """Add matrix dimensions, merging with any already declared ones."""
        merged = {**self.matrix, **normalize_matrix(dims)}
        return dataclasses.replace(self, matrix=MappingProxyType(merged))

    def add_matrix_skip(
        self, predicate: SkipFn | None = None, /, **kwargs: Any
    ) -> Self:
        """Drop variants: kwargs AND-matched against dims, plus optional
        predicate. Multiple calls compose as OR."""
        rule = make_skip_rule(predicate, kwargs)
        if rule is None:
            return self
        return dataclasses.replace(self, skips=self.skips + (rule,))

    def with_label(self, fn: LabelFn) -> Self:
        """Override how each variant's label renders in reports."""
        return dataclasses.replace(self, label_fn=fn)

    # ----- metrics ----------------------------------------------------

    def with_metric(
        self, *metrics: IterationMetric | Factory[tuple[IterationMetric, ...]]
    ) -> Self:
        """Set the per-iteration metrics, each reading stdout."""
        if len(metrics) == 1 and callable(metrics[0]):
            fn = metrics[0]

            def build(
                ctx: Context[Any],
            ) -> tuple[tuple[IterationMetric, MetricSource], ...]:
                return tuple((m, StdoutMetricSource) for m in fn(ctx))

            return dataclasses.replace(self, iteration_metrics=build)
        out = dataclasses.replace(self, iteration_metrics=const(()))
        for m in cast("tuple[IterationMetric, ...]", metrics):
            out = out.add_metric(m)
        return out

    def add_metric(
        self,
        metric: IterationMetric,
        source: Literal["stdout", "stderr"] | MetricSource = "stdout",
    ) -> Self:
        """Append one per-iteration metric reading from `source` ("stdout",
        "stderr", or a `(ExecutionResult) -> str` extractor)."""
        # Runtime guard for callers not running a type checker. A
        # process metric would otherwise crash deep in extraction.
        if not isinstance(metric, IterationMetric):  # pyright: ignore[reportUnnecessaryIsInstance]
            _raise_builder_type_error(
                "with_metric/add_metric",
                "an IterationMetric",
                metric,
                "use with_process_metric for process metrics like Time or max_rss",
            )
        src = as_metric_source(source)
        current = self.iteration_metrics
        base = current if current is not UNSET else const(())

        def build(
            ctx: Context[Any],
        ) -> tuple[tuple[IterationMetric, MetricSource], ...]:
            return base(ctx) + ((metric, src),)

        return dataclasses.replace(self, iteration_metrics=build)

    def with_process_metric(self, *metrics: ProcessMetric) -> Self:
        """Set (replace) the whole-process metrics (peak RSS, total time, ...)."""
        for m in metrics:
            if not isinstance(m, ProcessMetric):  # pyright: ignore[reportUnnecessaryIsInstance]
                _raise_builder_type_error(
                    "with_process_metric",
                    "a ProcessMetric",
                    m,
                    "use with_metric for iteration metrics like Regex or FloatPerLine",
                )
        return dataclasses.replace(self, process_metrics=const(tuple(metrics)))

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
            merged[name] = v if v is not UNSET else getattr(self, name)
        merged["env"] = _merge_env(self.env, over.env)
        merged["matrix"] = _merge_matrix(self.matrix, over.matrix)
        merged["skips"] = self.skips + over.skips
        return dataclasses.replace(over, **merged)


_SHARED_FIELDS = tuple(f.name for f in dataclasses.fields(BuilderBase))
