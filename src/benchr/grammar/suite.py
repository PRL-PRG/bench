"""Suite: a named collection of Benchmarks with propagating defaults.

A ``Suite`` is a frozen value object. ``.with_*`` methods return a new Suite
whose member benchmarks have had the same ``.with_*`` applied — but only
*where the benchmark's value is still unset*, so per-benchmark overrides win
over suite defaults.

The matrix algebra:
  - ``Suite.with_matrix(**axes)`` attaches axes that propagate into every
    contained benchmark at materialize-time;
  - ``Suite.with_skip(...)`` likewise propagates skip rules.

Producers:
  ``.add(b)``                        append a Benchmark
  ``.add_all(*bs)``                  append many
  ``.from_files(path, pattern=...)`` discover files and turn each into a Benchmark
  ``.with_matrix(**axes)``           add matrix axes to every contained benchmark
  ``.with_skip(...)``                add skip rules to every contained benchmark
  ``.filter(pred)``                  keep matching benchmarks
  ``.named(new_name)``               rename
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from benchr.grammar.benchmark import (
    Benchmark,
    CommandFn,
    EnvFn,
    PathFn,
    SkipFn,
    _coerce_policy,
    bench,
)
from benchr.grammar.policy import FixedRuns, StoppingPolicy
from benchr.grammar.processor import Processor


# A function the Runner can call to materialize benchmarks given the ctx.
# Suite.from_files defers discovery to this hook so e.g. paths can depend on
# ctx.cwd. The Runner / CLI flattens these into concrete benchmarks at run time.
BenchFactory = Callable[[Any], list[Benchmark]]


@dataclass(frozen=True, slots=True)
class Suite:
    """A named, frozen collection of benchmarks and deferred factories."""

    name: str = ""
    benchmarks: tuple[Benchmark, ...] = ()
    factories: tuple[BenchFactory, ...] = ()

    # ----- producers -------------------------------------------------

    def named(self, name: str) -> Suite:
        return dataclasses.replace(self, name=name)

    def add(self, b: Benchmark) -> Suite:
        return dataclasses.replace(self, benchmarks=self.benchmarks + (b,))

    def add_all(self, *bs: Benchmark) -> Suite:
        return dataclasses.replace(self, benchmarks=self.benchmarks + tuple(bs))

    def factory(self, fn: BenchFactory) -> Suite:
        """Register a deferred factory; ``materialize(ctx)`` will call it."""
        return dataclasses.replace(self, factories=self.factories + (fn,))

    def from_files(
        self,
        root: Path | Callable[[Any], Path],
        *,
        pattern: str | None = None,
        recursive: bool = True,
        exclude: set[str] | None = None,
    ) -> Suite:
        """Discover files; each becomes a Benchmark with ``b.path`` set.

        ``root`` may be a callable so discovery depends on ctx (e.g. ``ctx.cwd``).
        Benchmark name is the path relative to root, without extension
        (forward-slash separated). ``pattern`` is a regex matched against the
        filename via ``re.search``.
        """
        compiled = re.compile(pattern) if pattern else None
        exclude_set = exclude or set()

        def factory(ctx: Any) -> list[Benchmark]:
            r = root(ctx) if callable(root) else root
            r = Path(r)
            out: list[Benchmark] = []
            if r.is_dir():
                entries = (
                    Path(d) / fn
                    for d, _, fns in r.walk()
                    for fn in fns
                ) if recursive else (c for c in r.iterdir() if c.is_file())
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

        return self.factory(factory)

    def filter(self, pred: Callable[[Benchmark], bool]) -> Suite:
        """Keep only benchmarks for which ``pred(b)`` is truthy. Wraps any
        deferred factories so the filter also applies post-discovery.
        """
        kept = tuple(b for b in self.benchmarks if pred(b))
        new_factories = tuple(Suite._wrap_filter(fn, pred) for fn in self.factories)
        return dataclasses.replace(self, benchmarks=kept, factories=new_factories)

    # ----- propagating defaults --------------------------------------

    def with_command(self, command: Sequence[str] | CommandFn) -> Suite:
        return self._map(lambda b: b.with_command(command) if b.command is None else b)

    def with_cwd(self, cwd: Path | PathFn) -> Suite:
        return self._map(lambda b: b.with_cwd(cwd) if b.cwd is None else b)

    def with_env(self, env: Mapping[str, str] | EnvFn) -> Suite:
        # Envs merge — suite env first, then benchmark env wins per key.
        def merge(b: Benchmark) -> Benchmark:
            if callable(env) or callable(b.env):
                # If either side is dynamic, we have to build a closure.
                old_env = b.env

                def new_env(bb: Benchmark, ctx: Any) -> Mapping[str, str]:
                    e0 = env(bb, ctx) if callable(env) else env
                    e1 = old_env(bb, ctx) if callable(old_env) else old_env
                    return {**e0, **e1}

                return b.with_env(new_env)
            return b.with_env({**env, **b.env})

        return self._map(merge)

    def with_timeout(self, timeout: float) -> Suite:
        return self._map(lambda b: b.with_timeout(timeout) if b.timeout is None else b)

    def with_process(self, *processors: Processor) -> Suite:
        return self._map(lambda b: b.with_process(*processors) if not b.processors else b)

    def with_warmup(self, p: StoppingPolicy | int, *, force: bool = False) -> Suite:
        """Propagate a warmup policy. By default fills only benchmarks still at
        the ``FixedRuns(0)`` default; ``force=True`` overrides every benchmark
        (used by the CLI ``--warmup`` global override)."""
        policy = _coerce_policy(p)
        return self._map(
            lambda b: b.with_warmup(policy) if force or b.warmup == FixedRuns(0) else b
        )

    def with_measure(self, p: StoppingPolicy | int, *, force: bool = False) -> Suite:
        """Propagate a measure policy. By default fills only benchmarks still at
        the ``FixedRuns(1)`` default; ``force=True`` overrides every benchmark
        (used by the CLI ``--runs`` global override)."""
        policy = _coerce_policy(p)
        return self._map(
            lambda b: b.with_measure(policy) if force or b.measure == FixedRuns(1) else b
        )

    def with_runs(self, n: int, *, force: bool = False) -> Suite:
        """Alias of ``with_measure(FixedRuns(n))`` — propagates to children."""
        return self.with_measure(n, force=force)

    def runs(self, n: int) -> Suite:
        """Shorthand for ``with_runs``. Mirrors ``Benchmark.runs``."""
        return self.with_runs(n)

    # ----- matrix / skip ---------------------------------------------

    def with_matrix(self, **axes: Sequence[Any]) -> Suite:
        """Add one matrix axis per kwarg to every contained benchmark.

        Each contained benchmark gets the axes appended to its own ``axes``
        list (so per-benchmark axes still compose with suite-level ones).
        See ``Benchmark.with_matrix``.
        """
        if not axes:
            return self
        return self._map(lambda b: b.with_matrix(**axes))

    def with_skip(
        self,
        predicate: SkipFn | None = None,
        /,
        **kwargs: Any,
    ) -> Suite:
        """Drop variants matching the rule, applied to every contained benchmark.

        Same shape as ``Benchmark.with_skip``: kwargs are AND-matched against
        axis values, optional ``predicate(bench) -> bool`` for complex cases.
        """
        if predicate is None and not kwargs:
            return self
        return self._map(lambda b: b.with_skip(predicate, **kwargs))

    # ----- materialization ------------------------------------------

    def materialize(self, ctx: Any) -> list[Benchmark]:
        """Return the concrete (post-expansion) benchmark list.

        Calls deferred factories, then expands each benchmark's matrix axes
        into one concrete Benchmark per surviving variant.
        """
        out: list[Benchmark] = []
        for b in self.benchmarks:
            out.extend(b.expand())
        for fn in self.factories:
            for b in fn(ctx):
                out.extend(b.expand())
        return out

    # ----- helpers --------------------------------------------------

    def _map(self, fn: Callable[[Benchmark], Benchmark]) -> Suite:
        new_bs = tuple(fn(b) for b in self.benchmarks)
        new_factories = tuple(Suite._wrap_map(f, fn) for f in self.factories)
        return dataclasses.replace(self, benchmarks=new_bs, factories=new_factories)

    @staticmethod
    def _wrap_map(
        factory: BenchFactory, fn: Callable[[Benchmark], Benchmark]
    ) -> BenchFactory:
        def wrapped(ctx: Any) -> list[Benchmark]:
            return [fn(b) for b in factory(ctx)]

        return wrapped

    @staticmethod
    def _wrap_filter(
        factory: BenchFactory, pred: Callable[[Benchmark], bool]
    ) -> BenchFactory:
        def wrapped(ctx: Any) -> list[Benchmark]:
            return [b for b in factory(ctx) if pred(b)]

        return wrapped


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def suite(name: str, *benchmarks: Benchmark) -> Suite:
    """Concise constructor: ``suite("LoxSuite", b1, b2, ...)``."""
    return Suite(name=name, benchmarks=tuple(benchmarks))
