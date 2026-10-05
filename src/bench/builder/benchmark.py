"""`BenchmarkBuilder`: a benchmark template that `create()` expands into one
resolved `Benchmark` per matrix variant."""

from __future__ import annotations

import dataclasses
import itertools
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.builder.base import (
    BuilderBase,
    merge_mapping,
)
from bench.builder.context import Context, Data
from bench.builder.default import default_label, default_success
from bench.core.outlier import ModifiedZScore
from bench.core.policy import FixedRuns
from bench.model.benchmark import Benchmark, Variant
from bench.model.invocation import Invocation
from bench.params import Params
from bench.runner import Controller

# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BenchmarkBuilder(BuilderBase):
    """A benchmark spec that `.create()` expands into one resolved `Benchmark`
    per surviving variant. `data` holds arbitrary user-supplied values."""

    name: str = ""
    data: Mapping[str, Any] = dataclasses.field(default_factory=dict[str, Any])

    # ----- with_* setters -----------

    def with_data(self, **data: Any) -> BenchmarkBuilder:
        """Attach static data, readable as `ctx.data.<key>` and `b.data["<key>"]`.

        Merges with any data already set (later keys win). Values are stored
        verbatim; use `.with_matrix(...)` to expand a dimension into variants."""
        return self.replace(
            "data",
            data,
            override=False,
            merge=merge_mapping,
        )

    # ----- creation ----------------------------------------------------

    def create(self, params: Params, *, suite: str) -> Iterator[Benchmark]:
        """Yield one resolved `Benchmark` per matrix variant (cartesian product)
        that passes every filter."""
        bench_ctx: Context[Params] = Context(
            params=params,
            suite=suite,
            benchmark=self.name,
            data=Data(self.data),
        )

        # Resolve callable axes once, before expanding the product. The axis
        # Context has no per-variant matrix yet (we are defining it), so axes
        # can read params/suite/benchmark but not sibling axes.
        names = list(self.matrix)
        axes = [tuple(v(bench_ctx)) for v in self.matrix.values()]
        for combo in itertools.product(*axes):
            chosen = dict(zip(names, combo))
            variant = Variant.of(chosen)

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
        ctx: Context[Params],
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
                f"Benchmark {self.name} (suite {suite}) is missing a command!"
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
            inherit_env=self.inherit_env,
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

        hooks = [h(ctx) for h in self.hooks]

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
            hooks=hooks,
            data=self.data,
            label_fn=label_fn,
        )


# ---------------------------------------------------------------------------
# Shorthand constructors
# ---------------------------------------------------------------------------


def bench(name: str, **data: Any) -> BenchmarkBuilder:
    """A `BenchmarkBuilder` with attached data: sugar for
    `BenchmarkBuilder(name=name).with_data(**data)`."""
    return BenchmarkBuilder(name=name).with_data(**data)


def from_files(
    root: str | Path,
    *,
    pattern: str | None = None,
    recursive: bool = True,
    exclude: set[str] | None = None,
) -> list[BenchmarkBuilder]:
    """One `bench(name, path=file)` per file under `root` (or `root` itself if it
    is a file), named by its relative path without suffix."""
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
