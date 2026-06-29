"""Benchmark grammar: a builder template and the resolved instances it produces.

`BenchmarkBuilder` is the builder for `Benchmark`, one fully resolved variant. The
builder leaves every inheritable field unset. The resolved benchmark carries concrete
objects and a frozen `Execution`, which can be run.

Every configurable field is set either as a static value or as a `Build[T]` =
`(ctx) -> value` builder, resolved once per variant.

`create()` expands the matrix (cartesian product of the declared dimensions),
drops skipped cells, and resolves every field against the variant `Context` in
a single pass. Variants within a benchmark are what the end-of-run Summary
compares. Comparison across different benchmarks is never emitted.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, cast

from bench.core.execution import (
    EMPTY_MAPPING,
    Execution,
    SuccessFn,
    Variant,
    format_variant,
    to_argv,
)
from bench.core.metric import (
    IterationMetric,
    MetricSource,
    ProcessMetric,
    StdoutMetricSource,
    Time,
    as_metric_source,
)
from bench.core.outlier import OutlierDetection
from bench.core.policy import StoppingPolicy, coerce_policy
from bench.grammar.context import Cli, Context, Matrix

if TYPE_CHECKING:
    from _typeshed import StrOrBytesPath

    from bench.runner.source import HarnessMonitor

# A field builder: a `(ctx) -> value` resolved once per variant at create()
# time. The single concept for "value or function": a setter accepts `T |
# Build[T]`, where a callable is the builder and anything else is the static
# value (wrapped).
type Build[T] = Callable[[Context[Any]], T]

type CommandFn = Build[Sequence[StrOrBytesPath]]
type PathFn = Build[Path]
type EnvFn = Build[Mapping[str, str]]

# A matrix axis: either an explicit sequence of values or a
# `(ctx) -> sequence` callable resolved once per benchmark at `create()` time.
# The callable sees `ctx.params`/`ctx.suite`/`ctx.benchmark` but an empty
# matrix (sibling axes are not yet resolved). Normalized to the second member
# of `MatrixAxisValues` for storage.
type MatrixAxis = Sequence[Any] | Build[Sequence[Any]]
type MatrixAxisValues = tuple[Any, ...] | Build[Sequence[Any]]

# A label function turns a resolved benchmark into the human-readable variant
# identifier shown in reports (e.g. `"sleep 0.05"`). It takes the resolved
# Benchmark (not a Context) because labels reflect the resolved execution.
type LabelFn = Callable[[Benchmark], str]

# A skip predicate. Returning truthy drops the variant. Predicate receives the
# variant-stamped factory cell so it can read `b.vm`, `b.size`, etc.
type SkipFn = Callable[[BenchmarkBuilder], bool]


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


def const(value: Any) -> Build[Any]:
    """Wrap a static value as a constant builder."""
    return lambda _ctx: value


def as_build(value: Any, normalize: Callable[[Any], Any] = lambda v: v) -> Build[Any]:
    """Coerce a setter argument into a `Build[T]`: a callable is the builder as
    is, anything else is the static value, normalized once and wrapped."""
    if callable(value):
        return cast("Build[Any]", value)
    return const(normalize(value))


def default_label(b: Benchmark) -> str:
    """Default variant label: the formatted `(k=v, ...)` tuple, no parens."""
    return format_variant(b.variant).strip(" ()")


type Command = StrOrBytesPath | Sequence[StrOrBytesPath] | CommandFn


def normalize_matrix(
    dims: Mapping[str, MatrixAxis],
) -> Mapping[str, MatrixAxisValues]:
    """Validate dimension names and freeze `{name: values}` into the canonical
    mapping shared by `BenchmarkBuilder` and `SuiteBuilder`. An explicit
    sequence is frozen to a tuple; a `(ctx) -> sequence` callable is kept as-is
    and resolved once per benchmark at `create()` time."""
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


class BuilderSetters:
    """Shared `with_*` setters for the two builders (`BenchmarkBuilder`,
    `SuiteBuilder`).

    Both are frozen dataclasses with the same field names; each setter returns
    a replaced copy, so the concrete `Self` type is preserved. Setters that
    differ between the two (`with_harness`, `with_stdin`, `with_shuffle`) stay
    on their own class.
    """

    __slots__ = ()

    if TYPE_CHECKING:  # only ever mixed into the two frozen dataclasses
        __dataclass_fields__: ClassVar[dict[str, Any]]
        command: CommandFn
        cwd: PathFn
        env: EnvFn
        timeout: Build[float | None]
        iteration_metrics: Build[tuple[tuple[IterationMetric, MetricSource], ...]]
        process_metrics: Build[tuple[ProcessMetric, ...]]
        success: SuccessFn
        warmup: Build[StoppingPolicy]
        runs: Build[StoppingPolicy]
        outlier_detection: OutlierDetection
        cooldown: float
        matrix: Mapping[str, MatrixAxisValues]
        skips: tuple[SkipFn, ...]
        label_fn: LabelFn

    # ----- command / environment / execution -------------------------

    def with_command(self, command: Command) -> Self:
        return dataclasses.replace(self, command=as_build(command, to_argv))

    def with_cwd(self, cwd: str | Path | PathFn) -> Self:
        return dataclasses.replace(self, cwd=as_build(cwd, Path))

    def with_env(self, env: Mapping[str, str] | EnvFn) -> Self:
        return dataclasses.replace(self, env=as_build(env, dict))

    def with_timeout(self, timeout: float | None | Build[float | None]) -> Self:
        return dataclasses.replace(self, timeout=as_build(timeout))

    def with_success(self, fn: SuccessFn) -> Self:
        """Override the success policy (returns a failure reason, or None)."""
        return dataclasses.replace(self, success=fn)

    # ----- policies ---------------------------------------------------

    def with_warmup(self, p: int | StoppingPolicy | Build[StoppingPolicy]) -> Self:
        """Set the warmup policy."""
        return dataclasses.replace(self, warmup=as_build(p, coerce_policy))

    def with_runs(self, p: int | StoppingPolicy | Build[StoppingPolicy]) -> Self:
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
        """Declare matrix dimensions (variants are their cartesian product).

        Each axis is either an explicit sequence of values or a
        `(ctx) -> sequence` callable resolved once per benchmark at create()
        time (it reads `ctx.params`/`ctx.suite`/`ctx.benchmark`, not sibling
        axes)."""
        return dataclasses.replace(self, matrix=normalize_matrix(dims))

    def add_matrix(self, **dims: MatrixAxis) -> Self:
        """Add matrix dimensions, merging with any already declared
        (cf. `with_matrix`, which replaces the whole matrix). An axis may be a
        sequence or a `(ctx) -> sequence` callable (see `with_matrix`)."""
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
        self, *metrics: IterationMetric | Build[tuple[IterationMetric, ...]]
    ) -> Self:
        """Set (replace) the per-iteration metrics, each reading stdout.

        Pass metric instances, or a single `(ctx) -> (m, ...)` builder for
        per-variant metrics. Use `add_metric` to pick a non-default source."""
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
        # Runtime guard for callers not running a type checker: a misfiled
        # process metric would otherwise crash deep in extraction.
        if not isinstance(metric, IterationMetric):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(
                f"with_metric/add_metric expects an IterationMetric, got "
                f"{type(metric).__name__}; use with_process_metric for "
                f"process metrics like Time or max_rss"
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
                raise TypeError(
                    f"with_process_metric expects ProcessMetrics, got "
                    f"{type(m).__name__}; use with_metric for iteration metrics "
                    f"like Regex or FloatPerLine"
                )
        return dataclasses.replace(self, process_metrics=const(tuple(metrics)))


@dataclass(frozen=True, slots=True)
class BenchmarkBuilder(BuilderSetters):
    """A benchmark *spec*: a builder-style API configuring a workload that
    `.create()` expands into one resolved `Benchmark` per surviving variant.

    `data` holds arbitrary user-supplied keyword args, readable as attributes.
    Every inheritable field defaults to unset and so it will inherit the suite's
    default unless explicitely set.
    """

    name: str

    command: CommandFn = UNSET
    cwd: PathFn = UNSET
    env: EnvFn = UNSET
    timeout: Build[float | None] = UNSET
    stdin: Build[bytes | None] = const(None)  # None = no stdin (never inherited)
    iteration_metrics: Build[tuple[tuple[IterationMetric, MetricSource], ...]] = UNSET
    process_metrics: Build[tuple[ProcessMetric, ...]] = UNSET
    success: SuccessFn = UNSET
    warmup: Build[StoppingPolicy] = UNSET
    runs: Build[StoppingPolicy] = UNSET
    outlier_detection: OutlierDetection = UNSET
    cooldown: float = UNSET
    harness: bool = UNSET
    monitor: HarnessMonitor | None = UNSET

    data: Mapping[str, Any] = EMPTY_MAPPING
    matrix: Mapping[str, MatrixAxisValues] = EMPTY_MAPPING

    skips: tuple[SkipFn, ...] = ()

    label_fn: LabelFn = UNSET

    # ----- attribute access into data ---------------------------------

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)

    # ----- with_* setters (shared ones live on BuilderSetters) --------

    def with_stdin(self, data: bytes | str | Build[bytes]) -> BenchmarkBuilder:
        return dataclasses.replace(
            self,
            stdin=as_build(data, lambda d: d.encode() if isinstance(d, str) else d),
        )

    def with_harness(self, monitor: HarnessMonitor | None = UNSET) -> BenchmarkBuilder:
        """Mark this benchmark as a *harness*: the command is executed once and
        streams all iterations, where each line (or framed block) becomes one
        observation.

        `monitor` frames the output stream into iterations."""
        return dataclasses.replace(self, harness=True, monitor=monitor)

    # ----- creation ----------------------------------------------------

    def create(
        self, params: Any, *, suite: str, cli: Cli | None = None
    ) -> Iterator[Benchmark]:
        """Yield one fully-resolved `Benchmark`.

        Expands the matrix (cartesian product), drops cells matched by any skip
        rule (before any builder runs), then resolves every field against the
        variant `Context`.
        """
        cli = cli or Cli()
        names = list(self.matrix)
        if not names:
            yield self._resolve_cell(params, suite, (), cli=cli)
            return
        # Resolve callable axes once, before expanding the product. The axis
        # Context has no per-variant matrix yet (we are defining it), so axes
        # can read params/suite/benchmark but not sibling axes.
        axis_ctx: Context[Any] = Context(
            params=params,
            suite=suite,
            benchmark=self.name,
            matrix=Matrix(),
            cli=cli,
        )
        axes = [tuple(v(axis_ctx)) if callable(v) else v for v in self.matrix.values()]
        for combo in itertools.product(*axes):
            chosen = dict(zip(names, combo))
            variant = tuple(sorted((k, _stringify(v)) for k, v in chosen.items()))
            cell = dataclasses.replace(
                self,
                data=MappingProxyType({**self.data, **chosen}),
                matrix=EMPTY_MAPPING,
            )
            if any(skip(cell) for skip in self.skips):
                continue
            yield cell._resolve_cell(params, suite, variant, cli=cli)

    def _resolve_cell(
        self, params: Any, suite: str, variant: Variant, *, cli: Cli | None = None
    ) -> Benchmark:
        """Resolve every field for one variant in a single pass: every builder
        sees the same `Context` (params + the suite/benchmark names + this
        variant's matrix values). No field reads another's resolved value."""
        ctx: Context[Any] = Context(
            params=params,
            suite=suite,
            benchmark=self.name,
            matrix=Matrix(dict(self.data)),
            cli=cli or Cli(),
        )
        env = self.env(ctx)
        execution = Execution(
            command=tuple(os.fsdecode(a) for a in self.command(ctx)),
            cwd=Path(self.cwd(ctx)),
            env=env if env else EMPTY_MAPPING,
            timeout=self.timeout(ctx),
            stdin=self.stdin(ctx),
        )
        iteration_metrics = self.iteration_metrics(ctx)
        process_metrics = self.process_metrics(ctx)
        # Bare benchmark (no metrics declared at all): measure wall time.
        if not iteration_metrics and not process_metrics:
            process_metrics = (Time(),)
        b = Benchmark(
            suite=suite,
            name=self.name,
            execution=execution,
            variant=variant,
            iteration_metrics=iteration_metrics,
            process_metrics=process_metrics,
            success=self.success,
            warmup=self.warmup(ctx),
            runs=self.runs(ctx),
            outlier_detection=self.outlier_detection,
            cooldown=self.cooldown,
            harness=self.harness,
            monitor=self.monitor,
            data=self.data,
        )
        return dataclasses.replace(b, variant_label=self.label_fn(b))


@dataclass(frozen=True, slots=True)
class Benchmark:
    """One fully-resolved benchmark variant."""

    suite: str
    name: str
    execution: Execution
    variant: Variant
    iteration_metrics: tuple[tuple[IterationMetric, MetricSource], ...]
    process_metrics: tuple[ProcessMetric, ...]
    success: SuccessFn
    warmup: StoppingPolicy
    runs: StoppingPolicy
    outlier_detection: OutlierDetection
    cooldown: float
    harness: bool
    monitor: HarnessMonitor | None
    data: Mapping[str, Any]
    # Filled by a follow-up `dataclasses.replace` once the benchmark exists
    # (the label fn needs the resolved Benchmark), so it keeps a default and
    # sits last.
    variant_label: str = ""

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)


def _stringify(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in cast("Sequence[object]", v))
    return str(v)


# ---------------------------------------------------------------------------
# bench(): shorthand constructor
# ---------------------------------------------------------------------------


def bench(name: str, **data: Any) -> BenchmarkBuilder:
    """Build a BenchmarkBuilder with arbitrary attached data.

    `bench("zoo", path=Path("zoo.lox"))` makes `b.path` available. To add
    matrix dimensions use `.with_matrix(...)`.
    """
    return BenchmarkBuilder(name=name, data=dict(data) if data else EMPTY_MAPPING)


def from_files(
    root: str | Path,
    *,
    pattern: str | None = None,
    recursive: bool = True,
    exclude: set[str] | None = None,
) -> list[BenchmarkBuilder]:
    """Discover files under `root`, each becomes a factory with `b.path` set."""
    compiled = re.compile(pattern) if pattern else None
    exclude_set = exclude or set()
    r = Path(root)
    out: list[BenchmarkBuilder] = []
    if r.is_dir():
        entries = (
            (Path(d) / fn for d, _, fns in r.walk() for fn in fns)
            if recursive
            else (c for c in r.iterdir() if c.is_file())
        )
        for fp in entries:
            if compiled and not compiled.search(fp.name):
                continue
            name = str(fp.relative_to(r).with_suffix(""))
            if name in exclude_set:
                continue
            out.append(bench(name, path=fp))
    elif r.is_file():
        if compiled is None or compiled.search(r.name):
            name = r.stem
            if name not in exclude_set:
                out.append(bench(name, path=r))
    else:
        raise FileNotFoundError(f"from_files root not found: {r}")
    return out
