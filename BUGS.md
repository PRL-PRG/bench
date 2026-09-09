# BUGS

Src bugs found while keeping the test suite in sync with the refactored
architecture. Tests are migrated to the current API and assert the *intended*
behavior; remaining failures are left **red on purpose** so each maps to a real
defect. **No `src/` files were modified by the test migration.**

> Status re-verified against the current `src/` on every sync pass. `src/` is
> actively edited, so re-run the repro for any item before acting. Reproduce
> from the repo root with `uv run python …` or the named test.
>
> Every red test carries a `# RED ON PURPOSE: BUG-N` comment (or a
> `# pyright: ignore[...]  # BUG-N` marker where the defect is a type error), so
> the failure list and this file stay in step.

**Current state:** `pyright` reports **0 errors** across `src/`, `tests/` and
`examples/`, `ruff` is clean and `pytest` is **fully green**. **No defect is
open**, so there is no red test and no `RED ON PURPOSE` marker anywhere in
`tests/`.

The pass before this one caught `summarize` mid-rewrite: it raised `KeyError`
for any metric without an outlier, so 94 of 439 tests plus every
`examples/*.py` and the CLI died inside it, and nothing could be ported until it
ran. All three defects that pass filed — the `KeyError`, the uncounted
`runs`/`warmup_runs`, and the dropped variant-label fallback — are now **fixed
in `src/` by the author**, and the tests were ported to the semantics that fix
settled: **one run is one execution**, with `warmup_runs` and `failures` as
subsets of `runs` rather than counts beside it. A `Stat` also carries
`type: StatType` (`Literal["iteration", "process"]`) now, so one variant ×
metric can yield two rows; `runs`/`warmup_runs`/`failures` stay on the shared
per-variant accumulator, and both rows report the same counts.

An earlier pass followed the stats/summary split: the analysis layer is now
`bench.core.stats` (compute: `summarize`, `compute_by_benchmark`,
`compute_ranking`, `compute_by_axis`, `compute_by_metric`) plus `bench.summary`
(render: `ByBenchmarkSummary`, `ComparisonSummary`,
`GeomeanComparisonSummary`, `ByMetricSummary`, `DefaultSummary`), and the views
return rich renderables instead of `list[str]`. `bench.console.render` became
`bench.console.styling`, where hand-rolled column layout gave way to a rich
`Table`. Test modules were renamed to match (`test_summary.py` ->
`test_stats.py`, `test_formatter.py` -> `test_summary.py`, `test_render.py` ->
`test_styling.py`).

Five defects were triaged over the pass and all five are closed. Four were
**fixed in `src/` by the author**: `about the same as <target>` losing its
target, the variant label no longer falling back to the variant pairs, markup no
longer being escaped, and then the escaping fix's uncovered branch — `tag` is
gone and `Styling.span` now escapes both the styled and the unstyled branch. The
fifth, the per-metric sample count, was **confirmed removed on purpose**: its
test pins the run-count-only line, and the docstrings that still promised it
(`counts_cell`, `Counts`) were corrected.

Unrelated stale tests were ported in the same pass: `GitProbe` now raises
unless `allow_failure=True`, the duplicate matrix axis error lists every axis,
the root facade exposes the analysis layer as the `bench.stats` module
(`from bench import stats`; `stats.summarize(...)`) rather than re-exporting its
symbols one by one, and `Statistics.metrics`/`.suite` are gone — narrowing is
the caller's own comprehension over `Stat.metric_key` now.

---

## ❗ Open defects

None.

---

## 🔍 Low-severity observations (no red test)

- **Three `summarize` docstrings still describe the pre-rewrite counting.**
  Now that one run is one execution: `summarize` (`core/stats/base.py:178`) says
  warmup iterations are "counted (`Stat.warmups`)" — the field is `warmup_runs`
  and it counts wholly-warmup *executions* — and (`:179`) that whole-process
  samples "add to the run count only for a process-only execution", which no
  longer happens at all. `Stat` (`:87`) calls `runs`/`failures` "the variant's
  successful/failed iteration counts", but `runs` is executions and includes both
  the failed and the warmup ones. `Counts` (`:379`) is back to documenting a
  `samples` field it does not have — that text was corrected in an earlier pass
  and the rewrite reverted it. There is a `# FIXME: sync when (if ?)
  iteration/execution warmup split lands` at the site, so the area is known.
- **No way to discard leading iterations inside one execution.**
  `Iteration.warmup` still exists, the Controller still stamps it, and
  `summarize` still excludes flagged iterations from the stats while counting
  the execution as a warmup run (`core/stats/base.py:212`, `:222`) — but nothing
  can ever set it for a *subset*
  of one execution's iterations. `run_benchmark` (`runner/controller.py:197`)
  flags **every** iteration of an execution while the warmup policy is
  unsatisfied, and observes the policy once per execution, so `with_warmup(2)`
  means "two extra whole processes", not "drop the first two iterations".
  ```python
  # one process printing 5 values, warmup=2
  # want: 1 execution,  iterations flagged [True, True, False, False, False]
  # get:  3 executions of 5 iterations each, the first 2 flagged in full:
  #       [[T,T,T,T,T], [T,T,T,T,T], [F,F,F,F,F]]
  ```
  That is precisely the harness shape: one process prints N measurements and the
  leading ones are the JIT warming up. The examples work around it by having the
  harness discard its own warmup (`examples/workloads/fakevm.py -w`, Renaissance
  `-r`), so bench never sees it. Fix, if it is ever wanted: let the warmup policy
  observe iterations, or add a "first N iterations of each execution are warmup"
  setting. The test that pinned it is kept but **skipped**, not red —
  `test_iterations.py::test_leading_iterations_can_be_marked_warmup`. There is a
  `# TODO: Move warmup to execution (?)` at the site, so this is known.
- **The axis-issue line still speaks the pre-split vocabulary.** `_issue_line`
  (`summary/comparison.py:97`) labels itself `Summary - <axes>` while
  every block header now reads `Comparison - <axes> - <scope>`
  (`summary/comparison.py:119`), and it joins the axis names with `","` where
  `_axis_name` uses `", "`. So one render can show both `interp,mode` and
  `interp, mode`. Quoting is inconsistent within the four messages too: three
  interpolate the axis name bare, `bad_ref` uses `!r`.
- **`SummaryReporter`'s parameter is still called `formatter`.**
  `report/summary.py:30` takes `formatter: Summary | None` and stores
  `self._formatter`. The type and the docstring moved to the `Summary`
  vocabulary; the keyword did not, and it is public
  (`SummaryReporter(formatter=...)`).
- **`--list` renders nested suites flat.** `_list_planned_benchmarks`
  (`builder/app.py:440`) groups by the `suite` string, so a sub-suite shows up
  as one `Parent/Child` node beside its parent rather than nested under it. The
  tree it builds is `suite -> benchmark -> variant`, which is what its docstring
  promises; whether the new hierarchy should be reflected is a design call, not
  a defect.
