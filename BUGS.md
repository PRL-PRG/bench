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

**Current state:** `pyright` reports **1 error**, in `examples/` (see the
`default_reporter` smell under *Low-severity observations*); `src/` and `tests/`
are clean. `pytest` reports **12 failures**, all listed below — 11 for BUG-13 and
one for BUG-22. The aggregate failure set equals the union of the isolated
per-file runs.

This pass filed five new defects, all regressions from the CLI/reporter
decoupling (`361f069`, `5c4ca81`), and every one of them is already resolved:
BUG-23, BUG-24, BUG-26 and BUG-27 were fixed in the working tree as the pass ran
(between them, every `bench run` printed its summary three times and then
crashed, and `--show` ignored the app's own reporter), and BUG-25 was closed as
intended. What is left is the two long-standing entries below.

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
  `any(k not in b.data or b.data[k] != v ...)` (`builder/base.py:337`), so a skip
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
  (`builder/base.py:410`), and `with_env` works at every level:
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
  above the branch (`report/reporter.py:436`) and both paths share it. Covered by
  `test_reporter.py::test_progress_plain_lines_in_non_tty`,
  `test_reporter.py::test_progress_plain_count_scopes_per_benchmark` and
  `test_cli.py::test_bench_non_tty_shows_plain_progress`. (The `_local` state it
  lives in has no default, which is what **BUG-27** trips over on replay.)
- **BUG-18 — TTY progress crashed on a single-benchmark plan.** `_TUI` declared
  `overall_task` without assigning it, and `start()` only set it for plans of
  more than one benchmark, so `benchmark_done` raised `AttributeError`.
  `_TUI.__init__` initializes it to `None`. Covered by
  `test_reporter.py::test_progress_prints_completed_summary_scrollback`.
- **BUG-19 — a negative `line` leaked into `Sample.iteration`.**
  `FloatPerLine.process_text` reused one `idx` for both line selection and
  iteration numbering, so `.last_line()` emitted `iteration=-1` and the
  Controller then indexed `iterations[-1]` of an empty list. Selection now uses a
  separate `select_idx` and the iteration counter starts at 0. Covered by
  `test_metric.py::test_last_line_indexes_the_first_iteration`.
- **BUG-21 — filters were ignored for benchmarks without a matrix.**
  `BenchmarkBuilder.create` had a no-matrix fast path that yielded without
  consulting `self.filters`, so `with_filter`/`add_matrix_skip` only bit on
  matrix-expanded variants. The fast path is gone — a zero-axis
  `itertools.product` yields the single variant through the same filtered loop
  (`builder/benchmark.py:104`, filtered at `builder/benchmark.py:119`). The
  routing was never at fault: `filters` is a mergeable field, so `inherit_from`
  already carried it app → suite → benchmark. Covered by
  `test_suite.py::test_filter_without_matrix_drops_variant` and
  `test_runner.py::test_with_filter_bare_predicate_narrows_plan`.
- **BUG-23 — `CompositeReporter` re-delivered every event to the composite it had
  just flattened.** The helper added by `5c4ca81` yielded the expanded composite
  *in addition to* its children, because `yield r` sat outside the `isinstance`
  branch. So `CompositeReporter(CompositeReporter(P, J), S)` held
  `[P, J, Composite(P,J), S]` and every event reached `P` and `J` twice —
  `ProgressReporter.benchmark_done` removed its rich task on the first delivery
  and raised `KeyError` on the second, which crashed **every `bench run`**
  (`cli.py:246` wraps `default_reporter(ctx)` in a second composite and
  `run.py:207` wraps that again). A `return` after the recursive expansion
  (`report/reporter.py:98`) ends the walk, so a composite contributes only its
  leaves:
  ```python
  inner = CompositeReporter(ProgressReporter(), JsonReporter(Path("/tmp/x.json")))
  [type(r).__name__ for r in CompositeReporter(inner, ProgressReporter()).reporters]
  # ['ProgressReporter', 'JsonReporter', 'ProgressReporter']
  ```
  The four `test_cli_environment.py` tests that were failing on the crash
  (`test_run_check_environment_embeds_environment`,
  `test_run_omits_environment_by_default`,
  `test_run_check_environment_csv_has_comments`,
  `test_run_csv_has_no_comments_by_default`) are green. The duplicate *summary*
  on `bench run` is a separate defect — see **BUG-26**.
- **BUG-24 — `NoBenchmarksMatchedError` was exported but never raised.** The type
  was public (`run.py:70`, re-exported at `src/bench/__init__.py:101`) with a
  docstring naming exactly one situation, while `run` raised a bare `ValueError`;
  `361f069` had dropped the raise site along with its guard. `run.py:232` raises
  the dedicated type again, so a caller can catch just the empty-selection case.
  Covered by `test_cli.py::test_empty_selection_raises`.
- **BUG-26 — an app-supplied reporter gained a default summary.** `bench_app`'s
  docstring promises that `reporter=` "replace[s] the whole reporter (progress
  included)", but `run` checked only whether `summary` was set before appending
  the builtin one, and `use_defaults` is always `True` from `run_cli`. Since
  `bench run` is itself such an app (`cli.py:246` builds a reporter that already
  carries a `SummaryReporter`), every invocation printed the summary table twice
  — three times before BUG-23 was fixed. The summary is now composed inside each
  reporter branch, so it is appended only when the reporter was defaulted too.
  Covered by `test_reporter.py::test_app_reporter_replaces_the_whole_reporter`
  and `test_reporter.py::test_summary_channel_keeps_progress_and_swaps_summary`
  (the swap channel still works).
- **Test isolation.** `tests/test_runner.py` used to poison every later-ordered
  file: `test_sigint_kills_shell_wrapped_subtree` raced a `threading.Timer`
  against a benchmark that exited instantly, so its `os.kill(getpid(), SIGINT)`
  landed *after* the test, taking pytest with it. Fixed in the test. The
  aggregate run is now authoritative again.

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
- **BUG-20 — benchmarks run with an empty environment unless they opt in.**
  `Invocation.inherit_env` defaults to `False` (`core/invocation.py:38`, mirrored
  by the builder field at `builder/base.py:150`) and `execute` builds
  `env = dict(exe.env)` (`core/process.py:147`), so a benchmark that sets neither
  `env` nor `inherit_env` hands the child no environment at all — no `PATH`.
  `argv[0]` still resolves (against the *invoker's* PATH, in `_resolve_command`),
  so this only bites once something shells out:
  ```python
  b = bench("x").with_command(["sh", "-c", "sleep 0.01"]).with_cwd(Path("/tmp")).with_runs(1)
  Sequential().run(plan([suite("S", b)], None)).executions[0].returncode   # 127
  b.with_inherit_env()                                                    # 0
  ```
  **This default is intentional** (owner's call, 2026-08-21): a benchmark's
  environment is declared, not ambient, which is what makes a run reproducible
  across machines. There are two ways to satisfy a command that shells out, and
  both are first-class:
  - name the variables it needs — `with_env({"PATH": os.environ["PATH"]})`. This
    is what `examples/` does, deliberately: passing the one variable the workload
    needs keeps the declared environment minimal and is the discipline the
    examples exist to demonstrate.
  - hand over the ambient environment wholesale — `with_inherit_env()`
    (`builder/base.py:228`), which reaches the invocation via `_resolve_cell`
    (`builder/benchmark.py:163`) and is a mergeable field combined with `_or`
    (`builder/base.py:437`), so setting it at any level turns it on for
    everything below. It landed in `baee7cb` and is verified working. The tests
    use this one (`tests/test_runner.py`, `tests/test_e2e.py`): they only need
    the subprocess to run, so the shorter call is the clearer statement of
    intent there.
- **BUG-27 — `--show` ignored the configured reporter, then crashed.** `run_cli`
  hardcoded `show_report(default_reporter(build_params), ...)`, discarding
  `self.reporter`, so an app's formatter never ran on a saved report — and the
  substituted default carried a `ProgressReporter`, which died on its
  thread-local counter (`AttributeError: 'Local' object has no attribute 'n'`)
  because the replay never calls `start()`. `361f069` had dropped the reporter
  argument that the old `_do_show(reporter, show)` took. The path is now a method
  that resolves the reporter the same way a run does — `do_show_report`
  (`run.py:290`) calls `self.get_reporter(build_params, use_defaults=True)`.
  Covered by
  `test_cli.py::test_script_show_replays_through_configured_reporter`.
- **BUG-25 — `--list` applies `--include`/`--exclude`.** `run_cli` computes
  `plan_benchmarks(..., use_defaults=True)`, which applies the selection
  predicate (`run.py:170`), before the `--list` branch reads it (`run.py:275`),
  so `--list` shows the *filtered* plan where it used to show everything.
  **This is intentional** (owner's call, 2026-08-21): `--list` answers "what will
  this command run", so it has to honour the same selection the run would. The
  test that asserted the old unfiltered listing was rewritten to assert the
  filtered one (`test_cli.py::test_list_reflects_include_exclude`).
- **BUG-11 — ProgressReporter dropped the "elapsed estimate" column.** CHANGES.md
  records this as a deliberate simplification. The test that asserted it is
  deleted; only the `ProgressReporter` class docstring still promises the column
  and is now stale.

---

## ❗ Open defects

Ordered by severity, not by number.

### BUG-13 — inheritance precedence is inverted (High)
The documented cascade is `defaults < app < suite < benchmark` (the more
specific level wins). It runs the other way: `SuiteBuilder.materialize` calls
`builder.inherit_from(self)` (`src/bench/builder/suite.py:96`) and
`inherit_from(over)` lets **`over`** win every non-mergeable field
(`builder/base.py:382`, `override=True` at `builder/base.py:404`). So a suite
default silently overrides an explicit benchmark setting, and the same happens
one level up at app→suite.
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

### BUG-22 — no way to discard leading iterations inside one execution (Medium)
`Iteration.warmup` still exists, the Controller still stamps it, and
`summarize` still excludes flagged iterations from the stats while counting them
(`report/summary.py:115`) — but nothing can ever set it for a *subset* of one
execution's iterations. `run_benchmark` (`runner/controller.py:192`) flags
**every** iteration of an execution while the warmup policy is unsatisfied, and
observes the policy once per execution, so `with_warmup(2)` means "two extra
whole processes", not "drop the first two iterations".

That is precisely the harness shape: one process prints N measurements and the
leading ones are the JIT warming up.
```python
# one process printing 5 values, warmup=2
# want: 1 execution,  iterations flagged [True, True, False, False, False]
# get:  3 executions of 5 iterations each, the first 2 flagged in full:
#       [[T,T,T,T,T], [T,T,T,T,T], [F,F,F,F,F]]
```
Workaround in the examples: the harness discards its own warmup
(`examples/workloads/fakevm.py -w`, Renaissance `-r`), so bench never sees it.
Unlike the empty-environment default (BUG-20) this one has no builder-level
escape hatch.
Fix: let the warmup policy observe iterations, or add a "first N iterations of
each execution are warmup" setting.
Test: `test_iterations.py::test_leading_iterations_can_be_marked_warmup`.

---

## 🔍 Low-severity observations (no red test)

- **`default_reporter` returns `Reporter | None` but nothing accepts that.**
  `CompositeReporter(*reporters: Reporter)` rejects `None`, so the natural
  composition does not typecheck and every caller repeats a None-dance
  (`cli.py:248-253` does it; `examples/external/cpython.py:177` does not, which
  is the one remaining `pyright` error). Either return a no-op reporter instead
  of `None`, or let `CompositeReporter` drop `None`s. Fixing the example is
  `/sync-tests` work — this pass does not write `examples/`.

### Resolved in earlier passes

- **`Variant` is a half-mapping** — `__getitem__` added, so `dict(variant)`
  works and `keys()` is no longer a trap.
- **CSV always writes a `runtime` row** — the always-present wall-clock row is
  named `elapsed` now, matching what `Time()` calls its sample, so a user metric
  named `runtime` no longer collides with it.
- **`Dry`'s docstring is stale** — the `[harness]` sentence is gone; the
  `[unbounded]` marker it describes alongside is still real.
- **`PerfStat`'s docstring named the wrong base** — it now says the combinators
  "come from bases unchanged" rather than naming `Metric` (`perf.py:38`).
- **`stream_process`'s env comment contradicted its code** — the streaming path
  gates `child_env |= os.environ` on `exe.inherit_env` (`core/process.py:359`)
  just as `execute` does, and the comment below it no longer promises that an
  empty env inherits the parent's.
- **Policies lost value equality** — `FixedRuns`/`MaxDuration`/
  `CoefficientOfVariation` compare by identity, not value. Intended: nothing
  needs to compare policies.
- **`ProgressReporter._local` has no defaults** — `Local(threading.local)` takes
  its fields from `reset()` on the `start`/`benchmark_start` path. Intended: a
  consumer that skips the lifecycle is the bug, not the missing default.
