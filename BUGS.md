# BUGS

Src bugs found while migrating the test suite to the refactored architecture.
Tests were migrated to the current API and assert the *intended* behavior;
remaining failures are left **red on purpose** so each maps to a real defect.
**No `src/` files were modified by the test migration.**

> Status as of the latest check. `src/` is being actively edited, so re-run the
> repro for any item before acting. Reproduce from the repo root with
> `uv run --extra dev python …` or the named test (use isolated per-file runs —
> the aggregate is polluted; see the note at the bottom).

## ✅ Fixed (verified)

- **BUG-1 — `with_metric` unusable.** `BuilderBase.with_metric` seeded its
  accumulator from the `UNSET` sentinel and raised `RuntimeError: benchmark field
  is unset` at materialize for any metric. **Fixed** — `with_metric` now works;
  the ~90 metric-setting tests across test_runner/reporter/cli/cli_environment/
  e2e/suite/benchmark that were bug-blocked now pass.
- **BUG-4 — Controller dropped zero-iteration executions.** `run_benchmark` now
  appends the execution unconditionally (`controller.py:213`), so failed /
  process-only runs reach the Report.
- **BUG-7 — iteration metrics did not index iterations.** `FloatPerLine`/`Regex`
  produced `iteration=None`, so samples were misrouted to `process_samples` and
  `execution.iterations` stayed empty. **Fixed** — both now extend
  `MonotonicIterationMetric` (`metric.py:178,230`) and index samples `0,1,2,…`.
  ⚠️ See **BUG-12** for a follow-on the fix introduced.

## ❗ Present (originally reported)

### BUG-2 — `PerfStat` never stores its `events` (High)
`PerfStat.__init__(self, direction=None)` (`src/bench/perf.py:45`) ignores the
events tuple callers pass (it lands in `direction`); `self.events` stays `()`.
`__post_init__` (`perf.py:48`, meant to reject empty events) never runs — `PerfStat`
is not a dataclass. Fix: `__init__(self, events, direction=None)` storing/validating
events.
```python
from bench import PerfStat
PerfStat(("cache-misses","cache-references")).events   # () — should be the tuple
```
Tests: `test_perf.py` — `test_no_events_rejected`, `test_wrap_string_command`,
`test_wrap_list_command_keeps_args`, `test_extract_emits_one_sample_per_event`,
`test_extract_matches_modifier_suffix` (buggy signature also carries
`# pyright: ignore[reportArgumentType]  # BUG-2`).

### BUG-3 — `PerfStat` lacks direction combinators (Medium)
`class PerfStat(Metric)` (`perf.py:35`) — `.lower_is_better()`/`.higher_is_better()`
live on `BuildableMetric`, which `PerfStat` does not extend (its docstring claims
otherwise). `PerfStat(...).lower_is_better()` → `AttributeError`. Fix: extend
`BuildableMetric`. Test: `test_perf.py::test_lower_is_better_preserves_events_and_marks_samples`
(`# pyright: ignore[reportAttributeAccessIssue]  # BUG-3`).

### BUG-5 — `add_matrix_skip(**kwargs)` never drops variants (High)
`make_skip_rule` (`src/bench/builder/base.py:122`) matches kwargs against the
resolved `Benchmark` via `hasattr(b,k)/getattr(b,k)`, but matrix values live in
`Benchmark.data` and `Benchmark` no longer defines `__getattr__`, so `hasattr` is
always `False` and nothing is skipped. Fix: match against `b.data`.
```python
from pathlib import Path
from bench import bench, suite
b=(bench("x").with_command(["true"]).with_cwd(Path("/tmp"))
   .with_matrix(vm=["v8","jsc"],size=[100,500]).add_matrix_skip(vm="v8",size=500))
{(x.data["vm"],x.data["size"]) for x in suite("S",b).materialize(None)}  # ('v8',500) not skipped
```
Tests: `test_benchmark.py::test_add_matrix_skip_unions_rules_on_one_benchmark`,
`test_suite.py::test_with_skip_kwargs_drops_variant`,
`test_suite.py::test_suite_skip_unions_with_benchmark_skip`.

### BUG-6 — `Rebench` never emits non-runtime criteria (Low)
The criterion-emitting branch of `Rebench.process_text` is commented out
(`src/bench/core/metric.py:311-317`, `# TODO: criterions`); only `runtime` is
emitted. Test: `test_metric.py::test_rebench_metric` (expects `gc-rate`).
Arguably an incomplete feature; the test documents the intended contract.

## 🆕 Surfaced after the BUG-1 / BUG-7 fixes (need triage)

### BUG-8 — failed runs still emit metric samples (High)
`Controller.extract_execution` (`controller.py:127`) runs every metric regardless
of `result.failure`, so a failed run with a set runtime/output still yields
samples (e.g. a timed-out run emits `elapsed`). Two tests assert "failed runs emit
no metrics". Fix: skip metric extraction when `result.failure is not None`.
```python
from bench import Time
# make_failure(returncode=124, runtime=5.0) -> Time().process(...) yields ['elapsed']
```
Tests: `test_e2e.py::test_e2e_timeout_marks_failure`,
`test_runner.py::test_sequential_runs_bounded_policy_to_completion_despite_failures`.

### BUG-9 — failed run rendered "ok" (Medium)
For a zero-iteration execution the controller feeds the reporter a synthesized
`Iteration(samples=execution.process_samples)` (`controller.py:204`) that omits the
failure, so `ProgressReporter` prints `ok` for a failed run. Fix: carry
`failure=execution.failure` (and see BUG-10 for `runtime`). Test:
`test_reporter.py::test_progress_plain_marks_failures` (expects `FAIL` / `exit code 11`).

### BUG-10 — `MaxDuration` / `--time` never caps (High)
`_DurationState.observe` sums `iteration.runtime` (`policy.py:95`), but the
controller only sets `Execution.runtime` (`controller.py:40`); the iterations it
builds/synthesizes leave `Iteration.runtime == 0.0`. So a time budget never
accumulates and runs proceed to the run cap. Fix: stamp the run's runtime onto the
iteration(s). Test: `test_cli.py::test_bench_time_bound_caps_runs`
(`--runs 100 --time 0.3 "sleep 0.05"` ran all 100).

### BUG-12 — `MonotonicIterationMetric` counter leaks across runs (High; from the BUG-7 fix)
The `iteration` counter is instance state incremented in `get_sample`
(`metric.py:150-170`) and never reset per process; the same metric object is reused
across runs, so run *N*'s single-line output gets index *N-1*, creating spurious
empty iterations in later executions. Fix: reset per `process()` (or per run in the
controller / use a fresh metric per run).
```python
# reusing one FloatPerLine across runs of "echo 0.5":
# run1 -> [0], run2 -> [1], run3 -> [2]   (should be [0] each run)
```
Tests: `test_runner.py::test_sequential_three_runs_yields_three_samples`,
`test_e2e.py::test_e2e_warmup_then_measure`.

### BUG-11 (candidate) — ProgressReporter dropped the "elapsed estimate" column (Low)
The reporter no longer builds the per-iteration "elapsed estimate" column, though
its class docstring (`reporter.py:378`) still promises it — likely an accidental
drop during the harness removal. Test:
`test_reporter.py::test_eta_column_present_and_estimate_kept_for_command_bar`.
May instead be an intended simplification — confirm before acting.

## Test-side (stale expectations, not src bugs)

- `test_runner.py::test_dry_verbose_prints_full_block_per_execution` — asserted the
  verbose block shows `Time`; it shows the metric *name* `elapsed`. **Fixed in the
  test.**
- `test_reporter.py::test_csv_writer`, `test_json_writer_round_trip` — assume
  `elapsed` is "always measured", but the refactor made default metrics empty
  (`DEFAULTS.metrics = ()`, confirmed intended by
  `test_runner::test_default_metric_is_time`). The runtime rows pass; only the
  auto-`elapsed` assertion fails. **Not yet updated** — needs an explicit
  `Time()` in the shared `_s()` helper (or dropping the elapsed assertion); left
  as-is pending confirmation that no-auto-elapsed is the intended design.
- `test_runner.py::test_parallel_shared_report_not_corrupted_under_concurrency` —
  intermittently red; likely timing/concurrency flakiness rather than a fixed bug.

## Note — test isolation
The full-suite failure count is nondeterministic and lower than the sum of
isolated per-file runs: `tests/test_runner.py` leaks global state that masks
failures in later-ordered files (cumulative; exact mechanism not pinned). **Gate
on isolated per-file runs**, not the aggregate.
