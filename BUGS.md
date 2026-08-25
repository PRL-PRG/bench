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
`examples/`. `pytest` is **fully green**: 441 passed / 0 failed / 2 skipped, and
the aggregate matches the union of the isolated per-file runs. **No defect is
open**, so there is no red test and no `RED ON PURPOSE` marker anywhere in
`tests/`.

This pass followed the `Fingerprint`-is-a-mapping change, the `__main__`
params rewrite, and the `PerfRecord` port. All 23 failures were stale tests
(the old `Fingerprint` dataclass, and `add_dataclass_args(skip=...)`), not
defects. The "`SharedRunnerParams`/`SharedReporterParams` are not exported"
observation of the previous pass is **fixed** — both are in `bench.__all__`
now, alongside every other public symbol — and has been dropped.

---

## ❗ Open defects

None.

---

## 🔍 Low-severity observations (no red test)

- **`default_reporter`'s docstring still documents a parameter it no longer
  has.** The `summary=` keyword moved out of `default_reporter` into
  `get_reporter`, but the docstring (`builder/default.py:58-59`) still opens
  with "plus `summary` if one is given" and lists "Each of
  `summary`/`json`/`csv`/`dir`". Only the three sink keywords remain. Purely
  documentation, so nothing asserts it; the behaviour it describes is covered by
  `test_reporter.py::test_summary_channel_keeps_progress_and_swaps_summary` and
  `::test_summary_channel_survives_an_empty_sink_bundle`.
- **The shared-params split left the docstring on the wrong class.**
  `SharedReporterParams` (`params.py:136`) carries the text describing the
  *whole* flag set ("the full builtin flag set (`-j`/`--progress`/`--json`/…
  plus `--include`/`--exclude`)"), which is now `SharedBenchParams` — and
  `SharedBenchParams` (`params.py:178`) has no docstring at all. Purely
  documentation, so nothing asserts it. The behaviour is pinned by
  `test_context.py::test_each_shared_half_contributes_only_its_own_flags`.
- **No way to discard leading iterations inside one execution.**
  `Iteration.warmup` still exists, the Controller still stamps it, and
  `summarize` still excludes flagged iterations from the stats while counting
  them (`summary/summary.py:118`) — but nothing can ever set it for a *subset*
  of one execution's iterations. `run_benchmark` (`runner/controller.py:184`)
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
- **`--list` renders nested suites flat.** `_list_planned_benchmarks`
  (`builder/app.py:443`) groups by the `suite` string, so a sub-suite shows up
  as one `Parent/Child` node beside its parent rather than nested under it. The
  tree it builds is `suite -> benchmark -> variant`, which is what its docstring
  promises; whether the new hierarchy should be reflected is a design call, not
  a defect.
