"""Benchmark grammar: a builder template and the resolved instances it produces.

Two types live here:

  - `BenchmarkSpec` — the *template* returned by `bench()`. It carries the
    builder state: `command`/`cwd`/`env`, the inheritable config
    (timeout/stdin/metrics/success/warmup/runs/harness/monitor), a set of
    *matrix dimensions* (`.with_matrix(vm=[...], size=[...])`) whose cartesian
    product defines the variants, optional *skip* rules, a `label_fn`, and
    arbitrary `data`. Every inheritable field defaults to the `UNSET` null
    object meaning "inherit the suite's default"; `Suite` fills those in.

  - `Benchmark` — one fully *resolved* variant produced by
    `BenchmarkSpec.create()`. Its `command`/`cwd`/`env`/`timeout`/`stdin`
    are frozen into a plain `Execution`; its `variant`/`variant_label` are
    computed; the behavioral config (success/warmup/runs/metrics/monitor) is
    carried as concrete objects. The runner consumes these directly — no
    further resolution, no callables to evaluate.

Symmetry: every configurable field may be set either as a static value or as a
`(ctx) -> value` builder, resolved once per variant at `create()` time. For
command/cwd/env and the plain-valued fields (timeout/stdin/metrics/warmup/runs)
a bare callable is auto-detected as a builder. For the fields whose value is
*itself* a function (success/monitor/label_fn) a bare callable is the value;
wrap it in `Dynamic(fn)` to make it a per-variant builder.

Within a benchmark, variants are what get compared in the end-of-run Summary;
comparison across different benchmarks is meaningless and is never emitted.
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
from typing import TYPE_CHECKING, Any, cast

from benchr.core.execution import (
    EMPTY_MAPPING,
    Execution,
    SuccessFn,
    Variant,
    default_success,
    format_variant,
)
from benchr.core.metric import Metric
from benchr.core.policy import FixedRuns, StoppingPolicy, coerce_policy
from benchr.grammar.context import Context, Matrix

if TYPE_CHECKING:
    from _typeshed import StrOrBytesPath

    from benchr.runner.source import HarnessMonitor

# A user-supplied command/cwd/env builder. Receives the Context for the
# benchmark being created (params + the resolved suite/benchmark properties
# + the variant `matrix`).
type CommandFn = Callable[[Context[Any]], Sequence[StrOrBytesPath]]
type PathFn = Callable[[Context[Any]], Path]
type EnvFn = Callable[[Context[Any]], Mapping[str, str]]

# A label function turns a resolved benchmark into the human-readable variant
# identifier shown in reports (e.g. `"sleep 0.05"`).
type LabelFn = Callable[[Benchmark], str]

# A skip predicate. Returning truthy drops the variant. Predicate receives the
# variant-stamped factory cell so it can read `b.vm`, `b.size`, etc.
type SkipFn = Callable[[BenchmarkSpec], bool]


# ---------------------------------------------------------------------------
# Dynamic: marks a field value as a `(ctx) -> value` builder, resolved once per
# variant at create() time. `Dyn[T]` is the "static value or builder" union.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dynamic[T]:
    """Wrap a `(ctx) -> value` so a field is resolved per variant at create()."""

    fn: Callable[[Context[Any]], T]


# A stored field value: a static `T` or a `Dynamic` builder.
type Dyn[T] = T | Dynamic[T]
# What a plain-valued `with_*` setter accepts: a static `T`, a bare
# `(ctx) -> T` builder (auto-detected), or an explicit `Dynamic[T]`.
type Settable[T] = T | Callable[[Context[Any]], T] | Dynamic[T]


# ---------------------------------------------------------------------------
# UNSET: the one null object meaning "inherit the suite's default".
#
# Every inheritable factory field defaults to it; `Suite._resolve` swaps in the
# suite's value. Any use of an unresolved field — calling it, reading an
# attribute, truth-testing it — raises instead of guessing.
# ---------------------------------------------------------------------------

_UNSET_MSG = (
    "benchmark field is unset (it inherits the suite's default) — resolve "
    "the factory via Suite.materialize() before use"
)


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


# Typed `Any` so fields keep their concrete declared types; misuse of an
# unresolved factory fails loudly at runtime in one place (above).
UNSET: Any = _Unset()


def _resolve_dynamic(value: Any, ctx: Context[Any]) -> Any:
    """Resolve a static-or-`Dynamic` field against `ctx`."""
    if isinstance(value, Dynamic):
        return cast("Callable[[Context[Any]], Any]", value.fn)(ctx)
    return value


def _static_or(value: Any, default: Any) -> Any:
    """`value` if it is a plain static, else `default` (used to fill the
    preliminary Context's config slots, which a config builder must not read)."""
    return default if isinstance(value, Dynamic) else value


def _coerce_value(value: Any, static: Callable[[Any], Any]) -> Any:
    """Plain-valued field: a bare callable is auto-detected as a builder; a
    static value is run through `static` (type coercion)."""
    if isinstance(value, Dynamic):
        return cast(Any, value)
    if callable(value):
        return Dynamic(value)
    return static(value)


def coerce_command(command: Command | Dynamic[Sequence[StrOrBytesPath]]) -> CommandFn:
    if isinstance(command, Dynamic):
        return command.fn
    if callable(command):
        return command
    # A bare str/bytes/PathLike is a one-element argv; a Sequence is full argv.
    static = (
        (command,) if isinstance(command, (str, bytes, os.PathLike)) else tuple(command)
    )
    return lambda _: static


def coerce_cwd(cwd: str | Path | PathFn | Dynamic[Path]) -> PathFn:
    if isinstance(cwd, Dynamic):
        return cwd.fn
    if callable(cwd):
        return cwd
    static = Path(cwd)
    return lambda _: static


def coerce_env(env: Mapping[str, str] | EnvFn | Dynamic[Mapping[str, str]]) -> EnvFn:
    if isinstance(env, Dynamic):
        return env.fn
    if callable(env):
        return env
    static = dict(env)
    return lambda _: static


def default_label(b: Benchmark) -> str:
    """Default variant label: the formatted `(k=v, …)` tuple, no parens."""
    return format_variant(b.variant).strip(" ()")


type Command = StrOrBytesPath | Sequence[StrOrBytesPath] | CommandFn


def normalize_matrix(
    dims: Mapping[str, Sequence[Any]],
) -> Mapping[str, tuple[Any, ...]]:
    """Validate dimension names and freeze `{name: values}` into the canonical
    `{name: (v, …)}` mapping shared by `BenchmarkSpec` and `Suite`."""
    for name in dims:
        if name.startswith("_"):
            raise ValueError(f"Matrix dimension {name!r} cannot start with '_'")
    return MappingProxyType({name: tuple(values) for name, values in dims.items()})


def make_skip_rule(
    predicate: SkipFn | None, kwargs: Mapping[str, Any]
) -> SkipFn | None:
    """Build a skip predicate from a predicate and/or AND-matched kwargs; `None`
    when neither is given (the caller then leaves its skip list untouched).

    A variant is dropped when the returned predicate is truthy; within one rule
    all kwargs must match AND the predicate (if any) must return truthy.
    """
    if predicate is None and not kwargs:
        return None
    return lambda b: (
        all(hasattr(b, k) and getattr(b, k) == v for k, v in kwargs.items())
        and (predicate is None or predicate(b))
    )


@dataclass(frozen=True, slots=True)
class BenchmarkSpec:
    """A benchmark *template*: a builder-style API configuring a workload that
    `.create()` expands into one resolved `Benchmark` per surviving variant.

    `data` holds arbitrary user-supplied keyword args, readable as attributes
    (`b.path`, `b.size`) via the `__getattr__` hook below — used by skip/label
    callables. Every inheritable field defaults to `UNSET` ("inherit the suite's
    default") and is filled by `Suite._resolve`.
    """

    name: str

    command: CommandFn = UNSET
    cwd: PathFn = UNSET
    env: EnvFn = UNSET
    timeout: Dyn[float | None] = UNSET
    stdin: Dyn[bytes | None] = None  # None = no stdin (never inherited)
    metrics: Dyn[tuple[Metric, ...]] = UNSET
    success: Dyn[SuccessFn] = UNSET
    warmup: Dyn[StoppingPolicy] = UNSET
    runs: Dyn[StoppingPolicy] = UNSET
    harness: bool = UNSET
    monitor: Dyn[HarnessMonitor | None] = UNSET

    data: Mapping[str, Any] = EMPTY_MAPPING
    matrix: Mapping[str, tuple[Any, ...]] = EMPTY_MAPPING

    # Skip rules; a variant is dropped if any rule matches it.
    skips: tuple[SkipFn, ...] = ()

    # Variant-label function turning the resolved benchmark into the label
    # shown in reports.
    label_fn: Dyn[LabelFn] = UNSET

    # ----- attribute access into data ---------------------------------

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when normal lookup fails; safe to use even
        # with slots.
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)

    # ----- with_* methods ---------------------------------------------

    def with_command(self, command: Command | Dynamic[Any]) -> BenchmarkSpec:
        return dataclasses.replace(self, command=coerce_command(command))

    def with_cwd(self, cwd: str | Path | PathFn | Dynamic[Path]) -> BenchmarkSpec:
        return dataclasses.replace(self, cwd=coerce_cwd(cwd))

    def with_env(
        self, env: Mapping[str, str] | EnvFn | Dynamic[Mapping[str, str]]
    ) -> BenchmarkSpec:
        return dataclasses.replace(self, env=coerce_env(env))

    def with_timeout(self, timeout: Settable[float | None]) -> BenchmarkSpec:
        """Set the per-run timeout in seconds (`None` = explicitly no timeout,
        overriding any suite default). Accepts a `(ctx) -> float | None`
        builder or a `Dynamic`."""
        return dataclasses.replace(self, timeout=_coerce_value(timeout, lambda v: v))

    def with_stdin(
        self, data: bytes | str | Callable[[Context[Any]], bytes] | Dynamic[bytes]
    ) -> BenchmarkSpec:
        """Feed `data` to the process's stdin (str is UTF-8 encoded). Accepts a
        `(ctx) -> bytes` builder or a `Dynamic`."""
        return dataclasses.replace(
            self,
            stdin=_coerce_value(
                data, lambda d: d.encode() if isinstance(d, str) else d
            ),
        )

    def with_metric(
        self,
        *metrics: Metric
        | Callable[[Context[Any]], tuple[Metric, ...]]
        | Dynamic[tuple[Metric, ...]],
    ) -> BenchmarkSpec:
        """Set (replace) the benchmark's metrics. Pass them statically
        (`with_metric(m1, m2, …)`), or a single `(ctx) -> (m, …)` builder /
        `Dynamic` for per-variant metrics."""
        items: tuple[Any, ...] = metrics
        if len(items) == 1:
            only = items[0]
            if isinstance(only, Dynamic):
                return dataclasses.replace(self, metrics=cast(Any, only))
            if callable(only):
                return dataclasses.replace(self, metrics=Dynamic(only))
        return dataclasses.replace(self, metrics=cast("tuple[Metric, ...]", metrics))

    def with_success(self, fn: SuccessFn | Dynamic[SuccessFn]) -> BenchmarkSpec:
        """Override the success policy (returns a failure reason, or None). A
        bare function is the policy; wrap a `(ctx) -> SuccessFn` in `Dynamic`
        for per-variant selection."""
        return dataclasses.replace(self, success=fn)

    def with_warmup(self, p: int | Settable[StoppingPolicy]) -> BenchmarkSpec:
        return dataclasses.replace(self, warmup=_coerce_value(p, coerce_policy))

    def with_runs(self, p: int | Settable[StoppingPolicy]) -> BenchmarkSpec:
        return dataclasses.replace(self, runs=_coerce_value(p, coerce_policy))

    def with_harness(
        self, monitor: HarnessMonitor | None | Dynamic[HarnessMonitor | None] = UNSET
    ) -> BenchmarkSpec:
        """Mark this benchmark as a *harness*: the command is executed once and
        streams all iterations — each line (or framed block) becomes one
        observation. The harness MAY use convergence policies; the runner kills
        the process mid-flight when the policy converges.

        `monitor` frames the output stream into iterations; it defaults to
        inheriting the suite, and an explicit `None` (or an unset suite) falls
        back to `line_monitor`. A bare monitor is the value; wrap a
        `(ctx) -> monitor` in `Dynamic` for per-variant selection."""
        return dataclasses.replace(self, harness=True, monitor=monitor)

    # ----- matrix / skip / label --------------------------------------

    def with_matrix(self, **dims: Sequence[Any]) -> BenchmarkSpec:
        """Declare the matrix dimensions (replaces any previously set).

        Pass every dimension in one call: `b.with_matrix(vm=["v8", "jsc"],
        size=[100, 500])` gives 4 variants (the cartesian product). Dimension
        values are arbitrary; `with_command`/`with_cwd`/`with_env` callables
        read them via `ctx.matrix.vm`, while `add_matrix_skip` predicates
        receive the factory and read them as `b.vm`.
        """
        return dataclasses.replace(self, matrix=normalize_matrix(dims))

    def add_matrix_skip(
        self,
        predicate: SkipFn | None = None,
        /,
        **kwargs: Any,
    ) -> BenchmarkSpec:
        """Add a rule that drops variants.
        Multiple `.add_matrix_skip(...)` calls compose as OR (any rule may drop a
        variant).
        """
        rule = make_skip_rule(predicate, kwargs)
        if rule is None:
            return self
        return dataclasses.replace(self, skips=self.skips + (rule,))

    def with_label(self, fn: LabelFn | Dynamic[LabelFn]) -> BenchmarkSpec:
        """Override how each variant's label renders in reports.

        `fn` receives the resolved benchmark, e.g.
        `with_label(lambda b: " ".join(b.execution.command))`.
        """
        return dataclasses.replace(self, label_fn=fn)

    # ----- creation ----------------------------------------------------

    def create(self, params: Any, *, suite: str) -> Iterator[Benchmark]:
        """Yield one fully-resolved `Benchmark` per surviving variant cell.

        Expands the matrix (cartesian product), drops cells matched by any skip
        rule (before any builder runs), then resolves every field against the
        variant `Context`. Config builders (timeout/metrics/warmup/runs/…) are
        resolved first — they may read `ctx.params` and the variant, not each
        other — so the full `Context` carries resolved policies for command/
        cwd/env builders (e.g. a harness command reading `ctx.runs`).
        """
        names = list(self.matrix)
        if not names:
            yield self._resolve_cell(params, suite, ())
            return
        for combo in itertools.product(*self.matrix.values()):
            chosen = dict(zip(names, combo))
            variant = tuple(sorted((k, _stringify(v)) for k, v in chosen.items()))
            cell = dataclasses.replace(
                self,
                data=MappingProxyType({**self.data, **chosen}),
                matrix=EMPTY_MAPPING,
            )
            if any(skip(cell) for skip in self.skips):
                continue
            yield cell._resolve_cell(params, suite, variant)

    def _resolve_cell(self, params: Any, suite: str, variant: Variant) -> Benchmark:
        mat = Matrix(dict(self.data))
        # Phase 1: resolve config against a preliminary ctx (config builders may
        # read params/variant, not sibling config — the unresolved slots below
        # are placeholders).
        prelim = Context(
            params=params,
            suite=suite,
            benchmark=self.name,
            runs=_static_or(self.runs, FixedRuns(1)),
            warmup=_static_or(self.warmup, FixedRuns(0)),
            timeout=_static_or(self.timeout, None),
            metrics=_static_or(self.metrics, ()),
            harness=_static_or(self.harness, False),
            success=_static_or(self.success, default_success),
            matrix=mat,
        )
        runs = _resolve_dynamic(self.runs, prelim)
        warmup = _resolve_dynamic(self.warmup, prelim)
        timeout = _resolve_dynamic(self.timeout, prelim)
        metrics = _resolve_dynamic(self.metrics, prelim)
        harness = _resolve_dynamic(self.harness, prelim)
        success = _resolve_dynamic(self.success, prelim)

        # Phase 2: full ctx with resolved config for command/cwd/env/stdin.
        ctx = Context(
            params=params,
            suite=suite,
            benchmark=self.name,
            runs=runs,
            warmup=warmup,
            timeout=timeout,
            metrics=metrics,
            harness=harness,
            success=success,
            matrix=mat,
        )
        env = self.env(ctx)
        execution = Execution(
            command=tuple(os.fsdecode(a) for a in self.command(ctx)),
            cwd=Path(self.cwd(ctx)),
            env=env if env else EMPTY_MAPPING,
            timeout=timeout,
            stdin=_resolve_dynamic(self.stdin, ctx),
        )
        b = Benchmark(
            suite=suite,
            name=self.name,
            execution=execution,
            variant=variant,
            metrics=metrics,
            success=success,
            warmup=warmup,
            runs=runs,
            harness=harness,
            monitor=_resolve_dynamic(self.monitor, ctx),
            data=self.data,
        )
        label_fn = _resolve_dynamic(self.label_fn, ctx)
        return dataclasses.replace(b, variant_label=label_fn(b))


@dataclass(frozen=True, slots=True)
class Benchmark:
    """One fully-resolved benchmark variant."""

    # TODO: move the defaults into suite
    suite: str
    name: str
    execution: Execution
    variant: Variant = ()
    variant_label: str = ""
    metrics: tuple[Metric, ...] = ()
    success: SuccessFn = default_success
    warmup: StoppingPolicy = FixedRuns(0)
    runs: StoppingPolicy = FixedRuns(1)
    harness: bool = False
    monitor: HarnessMonitor | None = None
    data: Mapping[str, Any] = EMPTY_MAPPING

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)


def matrix_command(ctx: Context[Any]) -> Sequence[str]:
    return list(ctx.matrix.command)


def matrix_cwd(ctx: Context[Any]) -> Path:
    return Path(ctx.matrix.cwd)


def matrix_env(ctx: Context[Any]) -> Mapping[str, str]:
    return dict(ctx.matrix.env)


def _stringify(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in cast("Sequence[object]", v))
    return str(v)


# ---------------------------------------------------------------------------
# bench(): shorthand constructor
# ---------------------------------------------------------------------------


def bench(name: str, **data: Any) -> BenchmarkSpec:
    """Build a BenchmarkSpec with arbitrary attached data.

    `bench("zoo", path=Path("zoo.lox"))` makes `b.path` available. To add
    matrix dimensions use `.with_matrix(...)`.
    """
    return BenchmarkSpec(name=name, data=dict(data) if data else EMPTY_MAPPING)


def from_files(
    root: str | Path,
    *,
    pattern: str | None = None,
    recursive: bool = True,
    exclude: set[str] | None = None,
) -> list[BenchmarkSpec]:
    """Discover files under `root`; each becomes a factory with `b.path` set.

    Returns the list eagerly — splat into `suite(name, *from_files(...))`, or
    wrap in `Suite.factory` when the root depends on the params
    (`.factory(lambda ctx: from_files(ctx.params.cwd / "benchmarks", pattern=...))`).
    Factory name is the path relative to `root` without extension
    (forward-slash separated). `pattern` is a regex matched against the
    filename via `re.search`.
    """
    compiled = re.compile(pattern) if pattern else None
    exclude_set = exclude or set()
    r = Path(root)
    out: list[BenchmarkSpec] = []
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
