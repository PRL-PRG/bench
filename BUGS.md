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

**Current state:** `pyright` is clean across `src/`, `tests/` and `examples/`.
`pytest` reports **12 failures**, all listed below. The aggregate failure set
now equals the union of the isolated per-file runs (the old ordering
interference is gone — see *Fixed* below).

---

## ✅ Fixed since the last pass (verified)

- **BUG-1 — `with_metric` unusable.** Seeded its accumulator from the `UNSET`
  sentinel and raised at materialize. `UNSET` is gone; `with_metric` accumulates
  correctly.
- **BUG-2 — `PerfStat` never stored its `events`.** `__init__(self, direction=…)`
  swallowed the events tuple into `direction`, and the `__post_init__` guard
  never ran because `PerfStat` is not a dataclass. `__init__` now takes
  `(events, direction)`, validates a non-empty tuple and assigns it
  (`perf.py:45`). The five `test_perf.py` tests that were bug-blocked pass, and
  their `# pyright: ignore[reportArgumentType]` markers are gone.
- **BUG-3 — `PerfStat` lacked direction combinators.** It extended `Metric`, not
  `BuildableMetric`, so `.lower_is_better()` raised `AttributeError`. It now
  extends `BuildableMetric` (`perf.py:35`), and `examples/perf_cache_misses.py`
  imports — the example suite is fully green. Covered by
  `test_perf.py::test_lower_is_better_preserves_events_and_marks_samples`.
- **BUG-4 — Controller dropped zero-iteration executions.** `run_benchmark`
  appends the execution unconditionally, so failed / process-only runs reach the
  Report.
- **BUG-5 — `add_matrix_skip` used the wrong quantifier, three times over.** It
  first never dropped anything (matching against attributes `Benchmark` no longer
  exposed), then dropped everything *except* the matched cell (a `True`-on-match
  rule fed to a keep-predicate), then dropped on *any* kwarg match instead of all
  of them (`all(mismatch)` where `any(mismatch)` was meant). `rule` is now
  `any(k not in b.data or b.data[k] != v ...)` (`builder/base.py:335`), so a skip
  drops exactly the cell matching all its kwargs and several skips union.
  Covered by `test_benchmark.py::test_add_matrix_skip_unions_rules_on_one_benchmark`,
  `test_suite.py::test_with_skip_kwargs_drops_variant` and
  `test_suite.py::test_suite_skip_unions_with_benchmark_skip`.
- **BUG-6 — `Rebench` never emitted non-runtime criteria.** The criterion branch
  is back:
  ```python
  from bench.core.metric import Rebench, StdoutMetricSource
  [s.metric for s in Rebench(StdoutMetricSource).process_text(
      "x: b: iterations=1 runtime: 5ms\nx: gc-rate: 3MB")]   # ['runtime', 'gc-rate']
  ```
- **BUG-7 — iteration metrics did not index iterations.** `FloatPerLine`/`Regex`
  now stamp `Sample.iteration`, so samples route into `execution.iterations`.
  (But see **BUG-19** for the negative-index case.)
- **BUG-10 — `MaxDuration` / `--time` never capped.** `_MaxDurationState.observe`
  sums `execution.runtime` (`core/policy.py:110`), which the controller does set.
- **BUG-12 — `MonotonicIterationMetric` counter leaked across runs.** The
  iteration counter is a local in `process_text`, not instance state.
- **BUG-14 — `with_env` crashed materialize whenever one side was unset.**
  `inherit_from` merged the mergeable fields unconditionally, so an env factory
  merged against the level above's `None` produced a lambda calling `None(ctx)`.
  `inherit_from` now skips a mergeable field whose incoming value is `None`
  (`builder/base.py:395`), and `with_env` works at every level:
  ```python
  from bench import bench, suite
  suite("S", bench("a").with_command(["true"]).with_env({"X": "1"})).materialize(None)
  ```
  Covered green by
  `test_suite.py::test_benchmark_env_without_suite_env_materializes`.
- **BUG-15 — CSV `value` column held the unit.** `CsvReporter.finalize` wrote
  `"value": sample.unit`, losing every measurement. It writes the value now.
  Covered by `test_reporter.py::test_csv_writes_sample_values`.
- **BUG-16 — CSV declared sample `extra` columns but never filled them.** The
  header came from `report.samples_extra_keys()` while no row carried the keys.
  The rows carry them now. A sample *without* a given extra key is written blank
  rather than `False` — `extra` is free-form, so absence is not falsehood, and
  `test_reporter.py::test_csv_includes_outlier_column` asserts that.
- **BUG-17 — plain-progress counter never advanced, then was off by one.** The
  `self._local.n += 1` first lived only on the TTY branch (so the plain lines
  printed `[0/N]` forever), then moved onto both branches but *after*
  `_print_plain`, so each line showed the pre-increment value. It is now hoisted
  above the branch (`report/reporter.py:467`) and both paths share it. Covered by
  `test_reporter.py::test_progress_plain_lines_in_non_tty`,
  `test_reporter.py::test_progress_plain_count_scopes_per_benchmark` and
  `test_cli.py::test_bench_non_tty_shows_plain_progress`.
- **BUG-18 — TTY progress crashed on a single-benchmark plan.** `_TUI` declared
  `overall_task` without assigning it, and `start()` only set it for plans of
  more than one benchmark, so `benchmark_done` raised `AttributeError`.
  `_TUI.__init__` initializes it to `None` (`report/reporter.py:359`). Covered by
  `test_reporter.py::test_progress_prints_completed_summary_scrollback`.
- **BUG-19 — a negative `line` leaked into `Sample.iteration`.**
  `FloatPerLine.process_text` reused one `idx` for both line selection and
  iteration numbering, so `.last_line()` emitted `iteration=-1` and the
  Controller then indexed `iterations[-1]` of an empty list. Selection now uses a
  separate `select_idx` (`core/metric.py:171`) and the iteration counter starts
  at 0. Covered by `test_metric.py::test_last_line_indexes_the_first_iteration`.
- **BUG-21 — filters were ignored for benchmarks without a matrix.**
  `BenchmarkBuilder.create` had a no-matrix fast path that yielded without
  consulting `self.filters`, so `with_filter`/`add_matrix_skip` only bit on
  matrix-expanded variants. The fast path is gone — a zero-axis
  `itertools.product` yields the single variant through the same filtered loop
  (`builder/benchmark.py:104`, filtered at `builder/benchmark.py:119`). The routing was never at fault: `filters` is a
  mergeable field, so `inherit_from` already carried it app → suite → benchmark.
  Covered by `test_suite.py::test_filter_without_matrix_drops_variant` and
  `test_runner.py::test_with_filter_bare_predicate_narrows_plan`.
- **Test isolation.** `tests/test_runner.py` used to poison every later-ordered
  file: `test_sigint_kills_shell_wrapped_subtree` raced a `threading.Timer`
  against a benchmark that exited instantly (see BUG-20), so its
  `os.kill(getpid(), SIGINT)` landed *after* the test, taking pytest with it.
  Fixed in the test. The aggregate run is now authoritative again.

## 🚫 Closed as intended (not defects)

- **BUG-8 — failed runs emit metric samples.** `Controller.extract_execution`
  runs every metric regardless of `result.failure`, so a failed run with a
  recorded runtime still yields samples (a timed-out run emits `elapsed`, a
  spawn failure `elapsed=0`). This is intended: a run that failed still has a
  measurable wall time, and callers filter on `Execution.failure` /
  `Report.failures` rather than on sample absence. The `assert _all_samples(...)
  == []` lines that asserted the opposite were dropped from
  `test_e2e.py::test_e2e_timeout_marks_failure`,
  `test_e2e.py::test_e2e_command_not_found_marks_failure` and
  `test_runner.py::test_sequential_runs_bounded_policy_to_completion_despite_failures`;
  those tests still assert the failure count and returncode, which is the part
  that matters.
- **BUG-9 — failed run rendered "ok".** `Iteration` no longer carries a failure
  at all; `ProgressReporter._print_plain` reads `Execution.failure` and prints
  `FAIL (exit code N)`. Covered green by
  `test_reporter.py::test_progress_plain_marks_failures`.
- **BUG-11 — ProgressReporter dropped the "elapsed estimate" column.** CHANGES.md
  records this as a deliberate simplification. The test that asserted it is
  deleted; only the `ProgressReporter` class docstring
  (`report/reporter.py:382`) still promises the column and is now stale.

---

## ❗ Open defects

### BUG-13 — inheritance precedence is inverted (High)
The documented cascade is `defaults < app < suite < benchmark` (the more
specific level wins). It runs the other way: `SuiteBuilder.materialize` calls
`builder.inherit_from(self)` (`src/bench/builder/suite.py:96`) and
`inherit_from(over)` lets **`over`** win every non-mergeable field
(`builder/base.py:368`, `override=True` at `builder/base.py:390`). So a suite default silently overrides
an explicit benchmark setting, and the same happens one level up at app→suite.
```python
from pathlib import Path
from bench import bench, suite
b = bench("x").with_command(["true"]).with_cwd(Path("/tmp")).with_runs(7)
suite("S", b).with_runs(3).materialize(None)[0].runs.max_runs()   # 3, should be 7
```
The same inversion flips `env` merge precedence (the suite's value wins per key)
and matrix-dimension order (`merge_matrix`'s "inner first" contract is fed the
arguments the wrong way round, so suite dimensions are stamped before the
benchmark's).
Tests: `test_suite.py` — `test_runs_preserves_benchmark_override`,
`test_suite_callable_runs_still_loses_to_benchmark_override`,
`test_with_env_merges`, `test_env_merge_both_callable`,
`test_suite_warmup_respects_explicit_zero`,
`test_suite_measure_respects_explicit_one`,
`test_suite_with_success_propagates_and_respects_override`,
`test_suite_with_label_propagates_and_respects_override`,
`test_suite_dimensions_append_after_benchmark_dimensions`.
Also `test_pacing.py::test_cooldown_benchmark_overrides_suite` and
`test_cli.py::test_bench_app_defaults_fill_suites_but_lose_to_overrides`.

### BUG-20 — benchmarks run with an empty environment, and `inherit_env` is unreachable (High)
`Invocation.inherit_env` defaults to `False` and `execute` builds
`env = dict(exe.env)` (`core/process.py:147`), so unless the user sets `env`
the child gets **no environment at all** — no `PATH`. `argv[0]` still resolves
(against the *invoker's* PATH, in `_resolve_command`), which hides the problem
until something shells out:
```python
bench("x").with_command(["sh", "-c", "sleep 1"])   # -> exit 127, "sleep: command not found"
```
Three things compound it:
- `BenchmarkBuilder._resolve_cell` never passes `inherit_env`, and no `with_*`
  setter exposes it, so the flag cannot be turned on from the builder API.
- `stream_process` (`core/process.py:362`) does the opposite — it falls back to
  `os.environ` when `env` is empty — so the two execution paths disagree.
- The workaround is `with_env({"PATH": os.environ["PATH"]})` on the suite (or
  any level — BUG-14 no longer restricts where it can go), which every example
  that shells out now does.

No dedicated test: the suite and the examples pass PATH explicitly
(`tests/test_runner.py::_PATH_ENV`, `.with_env({"PATH": ...})` in the examples).
Fix one of: default `inherit_env` to `True`, expose a `with_inherit_env` setter,
or make `execute` mirror `stream_process`.

### BUG-22 — no way to discard leading iterations inside one execution (Medium)
`Iteration.warmup` still exists, the Controller still stamps it, and
`summarize` still excludes flagged iterations from the stats while counting them
(`report/summary.py:115`) — but nothing can ever set it for a *subset* of one
execution's iterations. `run_benchmark` (`runner/controller.py:189`) flags
**every** iteration of an execution while the warmup policy is unsatisfied, and
observes the policy once per execution, so `with_warmup(2)` means "two extra
whole processes", not "drop the first two iterations".

That is precisely the harness shape: one process prints N measurements and the
leading ones are the JIT warming up.
```python
# one process printing 5 values, warmup=2
# want: 1 execution, iterations flagged [True, True, False, False, False]
# get:  3 executions of 5 iterations each, the first 2 entirely warmup
```
Workaround in the examples: the harness discards its own warmup
(`examples/workloads/fakevm.py -w`, Renaissance `-r`), so bench never sees it.
Unlike BUG-20 this one has no builder-level escape hatch.
Fix: let the warmup policy observe iterations, or add a "first N iterations of
each execution are warmup" setting.
Test: `test_iterations.py::test_leading_iterations_can_be_marked_warmup`.

---

## 🔍 Low-severity observations (no red test)

- **`PerfStat`'s docstring names the wrong base.** It says `direction` and the
  combinators "come from the `Metric` base unchanged" (`perf.py:38`); they come
  from `BuildableMetric`, which it extends as of the BUG-3 fix.
- **Policies lost value equality.** `FixedRuns`, `MaxDuration` and
  `CoefficientOfVariation` were frozen dataclasses and are now plain
  `__slots__` classes, so `FixedRuns(3) == FixedRuns(3)` is `False` — while
  `And`/`Or` are still dataclasses and *do* compare by value. Probably
  incidental; the tests compare `.max_runs()` instead.

### Resolved this pass

- **`Variant` is a half-mapping** — `__getitem__` added, so `dict(variant)`
  works and `keys()` is no longer a trap.
- **CSV always writes a `runtime` row** — the always-present wall-clock row is
  named `elapsed` now, matching what `Time()` calls its sample, so a user metric
  named `runtime` no longer collides with it.
- **`Dry`'s docstring is stale** — the `[harness]` sentence is gone; the
  `[unbounded]` marker it describes alongside is still real.
