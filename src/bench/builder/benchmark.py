"""Benchmark builder: a builder template and the resolved instances it produces.

`BenchmarkBuilder` is the builder for `Benchmark`, one fully resolved variant. The
builder leaves every inheritable field unset. The resolved benchmark carries concrete
objects and a frozen `Invocation`, which can be run.

Every configurable field is set either as a static value or as a `Factory[T]` =
`(ctx) -> value` builder, resolved once per variant.

`create()` expands the matrix (cartesian product of the declared dimensions),
resolves every field against the variant `Context`, then drops skipped
variants. Variants within a benchmark are what the end-of-run Summary
compares. Comparison across different benchmarks is never emitted.

The shared configuration base (`BuilderBase`), the `Factory[T]`/`UNSET` primitives,
and the matrix/skip helpers live in `bench.builder.base`.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from bench.core.invocation import (
    EMPTY_MAPPING,
    Invocation,
    SuccessFn,
    Variant,
    format_variant,
)
from bench.core.metric import (
    Metric,
)
from bench.core.outlier import OutlierDetection
from bench.core.policy import StoppingPolicy
from bench.builder.base import (
    Factory,
    BuilderBase,
    as_build,
    const,
)
from bench.builder.context import Context, Data
from bench.runner.controller import Controller


def default_label(b: Benchmark) -> str:
    """Default variant label: the formatted `(k=v, ...)` tuple, no parens."""
    return format_variant(b.variant).strip(" ()")


class _DataAttrs:
    """Expose a `data` mapping's keys as read-only attributes (`b.<key>`)."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)


@dataclass(frozen=True, slots=True)
class BenchmarkBuilder(BuilderBase, _DataAttrs):
    """A benchmark *spec*: a builder-style API configuring a workload that
    `.create()` expands into one resolved `Benchmark` per surviving variant.

    `data` holds arbitrary user-supplied keyword args, readable as attributes.
    Every inheritable field defaults to unset and so inherits the suite's
    default unless explicitly set.
    """

    name: str = ""
    stdin: Factory[bytes | None] = const(None)  # None = no stdin (never inherited)
    data: Mapping[str, Any] = EMPTY_MAPPING

    # ----- with_* setters (shared ones live on BuilderBase) -----------

    def with_data(self, **data: Any) -> BenchmarkBuilder:
        """Attach static key/value data, readable as `ctx.data.<key>` (and `b.<key>`).

        Merges with any data already set (later keys win). Values are stored
        verbatim - a list value stays a list. Use `.with_matrix(...)` to expand a
        dimension into variants."""
        return dataclasses.replace(self, data={**self.data, **data})

    def with_stdin(self, data: bytes | str | Factory[bytes]) -> BenchmarkBuilder:
        return dataclasses.replace(
            self,
            stdin=as_build(data, lambda d: d.encode() if isinstance(d, str) else d),
        )

    # ----- creation ----------------------------------------------------

    def create(self, params: Any, *, suite: str) -> Iterator[Benchmark]:
        """Yield one fully-resolved `Benchmark` per surviving matrix variant.

        Expands the matrix (cartesian product), resolves every field against the
        variant `Context`, then drops any variant matched by a skip rule.
        """
        names = list(self.matrix)
        if not names:
            yield self._resolve_cell(params, suite, ())
            return
        # Resolve callable axes once, before expanding the product. The axis
        # Context has no per-variant matrix yet (we are defining it), so axes
        # can read params/suite/benchmark but not sibling axes.
        axis_ctx: Context[Any] = Context(
            params=params,
            suite=suite,
            benchmark=self.name,
            data=Data(),
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
            benchmark = cell._resolve_cell(params, suite, variant)
            if any(skip(benchmark) for skip in self.skips):
                continue
            yield benchmark

    def _resolve_cell(
        self,
        params: Any,
        suite: str,
        variant: Variant,
    ) -> Benchmark:
        """Resolve every field for one variant in a single pass: every builder
        sees the same `Context` (params + the suite/benchmark names + this
        variant's matrix values). No field reads another's resolved value."""
        ctx: Context[Any] = Context(
            params=params,
            suite=suite,
            benchmark=self.name,
            data=Data(dict(self.data)),
        )
        env = self.env(ctx)
        invocation = Invocation(
            command=tuple(os.fsdecode(a) for a in self.command(ctx)),
            cwd=Path(self.cwd(ctx)),
            env=env if env else EMPTY_MAPPING,
            timeout=self.timeout(ctx),
            stdin=self.stdin(ctx),
        )
        metrics = self.metrics(ctx)
        b = Benchmark(
            suite=suite,
            name=self.name,
            invocation=invocation,
            variant=variant,
            metrics=metrics,
            success=self.success(ctx),
            warmup=self.warmup(ctx),
            runs=self.runs(ctx),
            outlier_detection=self.outlier_detection,
            cooldown=self.cooldown,
            controller=self.controller,
            data=self.data,
        )
        return dataclasses.replace(b, variant_label=self.label_fn(b))


@dataclass(frozen=True, slots=True)
class Benchmark(_DataAttrs):
    """One fully-resolved benchmark variant."""

    suite: str
    name: str
    invocation: Invocation
    variant: Variant
    metrics: tuple[Metric, ...]
    success: SuccessFn
    warmup: StoppingPolicy
    runs: StoppingPolicy
    outlier_detection: OutlierDetection
    cooldown: float
    controller: Controller
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

    `bench("zoo", path=Path("zoo.lox"))` makes `b.path` available. It is exact
    sugar for `bench("zoo").with_data(path=Path("zoo.lox"))`. To add matrix
    dimensions use `.with_matrix(...)`.
    """
    return BenchmarkBuilder(name=name).with_data(**data)


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
