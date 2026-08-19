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
from typing import Any, cast

from bench.builder.base import (
    BuilderBase,
    LabelFn,
    merge_mapping,
)
from bench.builder.context import Context, Data
from bench.core.invocation import (
    Invocation,
    SuccessFn,
    Variant,
    default_success,
    format_variant,
)
from bench.core.metric import (
    Metric,
)
from bench.core.outlier import ModifiedZScore, OutlierDetection
from bench.core.policy import FixedRuns, StoppingPolicy
from bench.runner.controller import Controller


def default_label(b: Benchmark) -> str:
    """Default variant label: the formatted `(k=v, ...)` tuple, no parens."""
    return format_variant(b.variant).strip(" ()")


@dataclass(frozen=True, slots=True)
class BenchmarkBuilder(BuilderBase):
    """A benchmark *spec*: a builder-style API configuring a workload that
    `.create()` expands into one resolved `Benchmark` per surviving variant.

    `data` holds arbitrary user-supplied keyword args, readable as attributes.
    Every inheritable field defaults to unset and so inherits the suite's
    default unless explicitly set.
    """

    name: str = ""
    data: Mapping[str, Any] = dataclasses.field(default_factory=dict[str, Any])

    # ----- with_* setters -----------

    def with_data(self, **data: Any) -> BenchmarkBuilder:
        """Attach static key/value data, readable as `ctx.data.<key>` (and `b.<key>`).

        Merges with any data already set (later keys win). Values are stored
        verbatim - a list value stays a list. Use `.with_matrix(...)` to expand a
        dimension into variants."""
        return self.replace(
            "data",
            data,
            override=False,
            merge=merge_mapping,
        )

    # ----- creation ----------------------------------------------------

    def create(self, params: Any, *, suite: str) -> Iterator[Benchmark]:
        """Yield one fully-resolved `Benchmark` per surviving matrix variant.

        Expands the matrix (cartesian product), resolves every field against the
        variant `Context`, then drops any variant matched by a skip rule.
        """
        bench_ctx: Context[Any] = Context(
            params=params,
            suite=suite,
            benchmark=self.name,
            data=Data(self.data),
        )

        names = list(self.matrix)
        if not names:
            yield self._resolve_cell(suite, (), bench_ctx)
            return

        # Resolve callable axes once, before expanding the product. The axis
        # Context has no per-variant matrix yet (we are defining it), so axes
        # can read params/suite/benchmark but not sibling axes.
        axes = [tuple(v(bench_ctx)) for v in self.matrix.values()]
        for combo in itertools.product(*axes):
            chosen = dict(zip(names, combo))
            variant = tuple(sorted((k, _stringify(v)) for k, v in chosen.items()))

            cell = dataclasses.replace(
                self,
                data=merge_mapping(self.data, chosen),
                matrix={},
            )
            cell_ctx = dataclasses.replace(
                bench_ctx,
                data=Data(merge_mapping(Data.as_mapping(bench_ctx.data), chosen)),
            )

            benchmark = cell._resolve_cell(suite, variant, cell_ctx)
            if not all(p(benchmark) for p in self.filters):
                continue

            yield benchmark

    def _resolve_cell(
        self,
        suite: str,
        variant: Variant,
        ctx: Context[Any],
    ) -> Benchmark:
        """Resolve every field for one variant in a single pass: every builder
        sees the same `Context` (params + the suite/benchmark names + this
        variant's matrix values). No field reads another's resolved value."""
        if self.env is None:
            env = dict[str, str]()
        else:
            env = self.env(ctx)

        if self.command is None:
            raise ValueError(
                f"Benchmark f{self.name} (suite {suite}) is missing a command!"
            )
        command = tuple(map(os.fsdecode, self.command(ctx)))

        if self.cwd is None:
            cwd = Path.cwd()
        else:
            cwd = self.cwd(ctx)

        if self.timeout is None:
            timeout = None
        else:
            timeout = self.timeout(ctx)

        if self.stdin is None:
            stdin = None
        else:
            stdin = self.stdin(ctx)

        invocation = Invocation(
            command=command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            stdin=stdin,
        )

        metrics = [m(ctx) for m in self.metrics]

        if self.success is None:
            success = default_success
        else:
            success = self.success(ctx)

        if self.warmup is None:
            warmup = FixedRuns(0)
        else:
            warmup = self.warmup(ctx)

        if self.runs is None:
            runs = FixedRuns(1)
        else:
            runs = self.runs(ctx)

        if self.outlier_detection is None:
            outlier_detection = ModifiedZScore()
        else:
            outlier_detection = self.outlier_detection

        if self.cooldown is None:
            cooldown = 0.0
        else:
            cooldown = self.cooldown

        if self.controller is None:
            controller = Controller()
        else:
            controller = self.controller(ctx)

        if self.label_fn is None:
            label_fn = default_label
        else:
            label_fn = self.label_fn

        return Benchmark(
            suite=suite,
            name=self.name,
            invocation=invocation,
            variant=variant,
            metrics=metrics,
            success=success,
            warmup=warmup,
            runs=runs,
            outlier_detection=outlier_detection,
            cooldown=cooldown,
            controller=controller,
            data=self.data,
            label_fn=label_fn,
        )


# TODO: Move to model
@dataclass(frozen=True, slots=True)
class Benchmark:
    """One fully-resolved benchmark variant."""

    suite: str
    name: str
    invocation: Invocation
    variant: Variant
    metrics: Sequence[Metric]
    success: SuccessFn
    warmup: StoppingPolicy
    runs: StoppingPolicy
    outlier_detection: OutlierDetection
    cooldown: float
    controller: Controller
    data: Mapping[str, Any]
    label_fn: LabelFn

    @property
    def variant_label(self) -> str:
        return self.label_fn(self)


def _stringify(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in cast("Sequence[object]", v))
    return str(v)


# ---------------------------------------------------------------------------
# Shorthand constructors
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
