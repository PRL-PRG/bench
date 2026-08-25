from __future__ import annotations

import itertools
import json
import threading
from pathlib import Path

from cattrs import unstructure

from bench.core.diagnostic import Diagnostic
from bench.core.fingerprint import Fingerprint
from bench.model.benchmark import (
    Benchmark,
    Variant,
    format_variant_pairs,
)
from bench.model.results import Execution, Report
from bench.report.base import Reporter


# TODO: Move somewhere else
def execution_dir(
    root: Path, suite: str, benchmark: str, leaf: str | int | Path
) -> Path:
    """The per-execution directory `<root>/<suite>/<benchmark>/<leaf>`, the one
    source of truth for the `--dir` layout.

    `leaf` is the matrix variant sub-path (see `variant_path`) when the benchmark
    has variants, else the 1-based completion ordinal `DirReporter` assigns per
    `(suite, benchmark)`.
    """
    return root / suite / benchmark / (leaf if isinstance(leaf, Path) else str(leaf))


# TODO: Move somewhere else
def variant_path(variant: Variant, *, nested: bool = False) -> Path:
    """The sub-directory a matrix `variant` maps to under its benchmark.

    Flat (the default): a single `dim1=val1, dim2=val2` component, so the tree
    stays one level deep whatever the matrix. Nested: one directory level per
    dimension, `dim1/val1/dim2/val2`. An empty variant maps to an empty path.
    """
    if not variant:
        return Path()
    if nested:
        return Path(*itertools.chain.from_iterable(variant))
    return Path(format_variant_pairs(variant))


class DirReporter(Reporter):
    """Per-execution tree at `<out>/<suite>/<bench>/<leaf>/` (see `execution_dir`).

    Files: stdout, stderr, exitcode, seq (cwd + cmd + info). For a matrix variant
    `leaf` is the variant sub-path - a flat `dim=val, ...` component by default, or
    nested `dim/val/...` when constructed with `nested=True`; a plain benchmark's
    runs count up per (suite, benchmark) in completion order.
    """

    def __init__(
        self,
        root: Path,
        *,
        nested: bool = False,
        fingerprint: Fingerprint | None = None,
        diagnostics: list[Diagnostic] | None = None,
    ) -> None:
        self.root = root
        self.nested = nested
        self.fingerprint = fingerprint
        self.diagnostics = diagnostics or []
        self._counters: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def output_dir(
        self, suite: str, benchmark: str, variant: Variant = Variant()
    ) -> Path:
        """Where this reporter writes a given execution's files."""
        return execution_dir(
            self.root, suite, benchmark, variant_path(variant, nested=self.nested)
        )

    def start(self, plan: list[Benchmark]) -> None:
        self._counters = {}
        self.root.mkdir(parents=True, exist_ok=True)
        # Pre-create the per-variant directories so a wrapped command (e.g. `perf
        # record -o <dir>/perf.data`) has somewhere to write before it runs. Only
        # variant benchmarks get a deterministic path up front; plain runs are
        # numbered lazily, in completion order, by execution_done.
        for b in plan:
            if b.variant:
                self.output_dir(b.suite, b.name, b.variant).mkdir(
                    parents=True, exist_ok=True
                )
        if self.fingerprint is not None:
            self._write_fingerprint(self.fingerprint, self.diagnostics)

    def execution_done(self, execution: Execution) -> None:
        # Stable path per variant, lazy per-run numbering otherwise (see start).
        if execution.variant:
            exec_dir = self.output_dir(
                execution.suite, execution.benchmark, execution.variant
            )
        else:
            key = (execution.suite, execution.benchmark)
            with self._lock:
                self._counters[key] = self._counters.get(key, 0) + 1
                n = self._counters[key]
            exec_dir = execution_dir(self.root, execution.suite, execution.benchmark, n)
        exec_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            f"cwd={execution.cwd}",
            f"command={' '.join(execution.command)}",
            f"run={execution.run}",
        ]
        lines.extend(f"variant[{k}]={v}" for k, v in execution.variant)
        if execution.variant_label:
            lines.append(f"variant_label={execution.variant_label}")
        (exec_dir / "seq").write_text("\n".join(lines) + "\n")

        (exec_dir / "stdout").write_text(execution.stdout)
        (exec_dir / "stderr").write_text(execution.stderr)
        (exec_dir / "exitcode").write_text(f"{execution.returncode}\n")

    def finalize(self, report: Report) -> None:
        if report.fingerprint is not None:
            self._write_fingerprint(report.fingerprint, report.diagnostics)

    def _write_fingerprint(
        self, fingerprint: Fingerprint, diagnostics: list[Diagnostic]
    ) -> None:
        (self.root / "fingerprint.json").write_text(
            json.dumps(
                {
                    "fingerprint": unstructure(fingerprint),
                    "diagnostics": unstructure(diagnostics),
                },
                indent=2,
            )
        )
