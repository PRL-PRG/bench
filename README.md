# benchr

A lightweight Python benchmarking framework.

Two ways to use it:

* **`benchr bench`** — hyperfine-style CLI for ad-hoc command timing.
* **`run(suite, …)`** — declarative Python scripts for repeatable benchmark
  configurations (file-discovered benchmarks, matrices, custom metrics,
  convergence policies).

---

## Quick start

### As a CLI

```console
$ benchr bench --runs 5 --warmup 1 'sleep 0.05' 'sleep 0.1'
[1|12] bench/sleep 0.05 #1 [warmup] ok
[2|12] bench/sleep 0.05 #1 [measure] ok
[3|12] bench/sleep 0.05 #2 [measure] ok
...
[12|12] bench/sleep 0.1 #5 [measure] ok

bench/sleep 0.05: 0|5 runs
  elapsed  (mean ± σ):  55.71 ± 1.98    (53.64 … 58.14)

bench/sleep 0.1: 0|5 runs
  elapsed  (mean ± σ):  108.05 ± 1.58    (105.39 … 109.39)

Summary
  'sleep 0.05' [elapsed] was
    1.97 ± 0.08 times lower than 'sleep 0.1'
```

`0|5 runs` means **0 failures | 5 successes**. The `elapsed` numbers are
milliseconds (auto-scaled from the seconds the `Time()` metric emits).

### As a script

```python
from benchr import Path, Time, bench, run, suite

s = (
    suite("demo",
        bench("fast").with_command(["sleep", "0.05"]),
        bench("slow").with_command(["sleep", "0.1"]),
    )
    .with_cwd(Path("."))
    .with_metric(Time())   # measure wall-clock elapsed
    .runs(5)               # 5 measured runs each
)

if __name__ == "__main__":
    run(s)
```

```console
python demo.py --runs 10 --json out.json
```

Every flag the CLI accepts (`--runs`, `--warmup`, `--jobs`, `--json`, `--csv`,
`--dir`, `--compare`, `--dry`, `--verbose`) also works on a script built with
`run(...)`. CLI flags **always override** what the script set in code
(`--runs N` forces `N` measured runs for every benchmark, replacing per-benchmark
values).

See [`examples/`](examples/) for one runnable script per capability.

---

## The model

A benchmark run is a pipeline. The blocks below are the things you build
(`Suite`, `Benchmark`) and the things a run produces (`RunRecord`, `Sample`,
`Report`). Everything in the right column is **pure data** — Reports round-trip
cleanly through JSON.

* `Suite`: a named collection of benchmarks
* `Benchmark`: command + cwd + end + metrics + stopping policy + variants
* `RunRecord`: one execution identity with outcome (failure or list of samples)
* `Sample`: one metric value
* `Report`: `[RunRecord]`, JSON round-trippable

### Pipeline

```
   Benchmark.compile()        ← coroutine, yields one ScheduledExecution per run
        │
        ▼
   ScheduledExecution         ← (Execution, suite, benchmark, run, phase, variant)
        │
        ▼
   execute()                  ← the only step that spawns a process
        │
        ▼
   ExecutionResult            ← stdout, stderr, returncode, runtime, rusage
        │
        ▼
   judge()                    ← SuccessFn → Verdict (None = ok, str = failure)
        │
   ┌────┴────────┐
   ▼             ▼
 failure       success
   │             │ Metric.process()
   │             ▼
   │           [Sample]
   │             │
   └─────┬───────┘
         ▼
   RunRecord(identity, outcome, samples)
         │
         ▼
   StoppingPolicy.observe(run, samples)
         │
         ▼ not converged → next ScheduledExecution
         │
         ▼ converged
     Report  →  Reporter (stream) + Formatter (final summary)
```

A **failed run still produces a `RunRecord`** (with `failure` set, empty
`samples`). That's why a failing benchmark appears in the summary as `3|0 runs`
(3 failures, 0 successes) with the reason listed in a `Failures:` block — no
fake metrics poisoning the stats. `.runs(N)` means "N attempts", so a crashing
benchmark stops after N runs rather than retrying.

### Suite vs Benchmark — who overrides whom

Suites propagate defaults to their members **only when the benchmark's field is
still unset**, so per-benchmark values always win over suite defaults:

```python
suite("S",
    bench("a").with_timeout(5),       # a keeps timeout=5
    bench("b"),                       # b gets timeout=30 from the suite
).with_timeout(30)
```

The CLI sits one layer above that: `--runs N` and `--warmup N` are *forcing*
overrides — they replace every benchmark's value regardless of what the script
set:

```
   bench(...).runs(10)               ← per-benchmark, wins over suite default
        ▲
   suite(...).runs(10)               ← suite default, fills unset benchmarks
        ▲
   CLI --runs 10                     ← forcing override, replaces every benchmark
```

Other CLI flags (`--json`, `--csv`, `--dir`, `--compare`) are **additive**: they
attach extra output sinks alongside whatever reporter the script configured.

### RunRecord and Sample

The single abstraction the runner produces is `RunRecord`. Each `RunRecord`
carries identity + outcome + nested `Sample`s:

```python
RunRecord(
    suite, benchmark, variant, run, phase,        # identity
    command, returncode, runtime, failure, message,  # outcome
    variant_label,
    samples: list[Sample],                        # parsed metrics (empty on failure)
)

Sample(metric, value, unit, lower_is_better)      # metric data only
```

* `variant` is a sorted tuple of `(axis, value)` pairs identifying the
  matrix cell (e.g. `(("compiler","gcc"),("opt","O2"))`). Empty tuple when
  the benchmark has no matrix.
* `variant_label` is the human-readable label of the variant (from
  `Benchmark.with_label(...)`, or the formatted `variant` tuple otherwise).
* `phase` is `"warmup"` or `"measure"`. Warmup runs appear in JSON/CSV/dir
  outputs but are excluded from stats.
* `Sample.lower_is_better` is set by the Metric — `True` for runtime, `False`
  for throughput, `None` when not comparable.

Every execution yields one `RunRecord`. A *successful* run carries one or more
`Sample`s (one per metric the Metric emitted); a failed run carries an empty
`samples` list and a non-None `failure`.

### Serialization

`Report` round-trips through JSON. Run any script with `--json out.json` and you
get:

```json
{
  "runs": [
    {
      "suite": "factory_demo",
      "benchmark": "tiny",
      "variant": [],
      "run": 1,
      "phase": "measure",
      "command": ["python3", "-c", "sum(range(1000))"],
      "returncode": 0,
      "runtime": 0.014422666048631072,
      "samples": [
        {
          "metric": "elapsed",
          "value": 0.014422666048631072,
          "unit": "s",
          "lower_is_better": true
        }
      ]
    }
  ]
}
```

Programmatically:

```python
from benchr import report_from_json, report_to_json

r = report_from_json(Path("out.json").read_text())
for run in r.runs:
    for s in run.samples:
        print(run.benchmark, run.run, s.metric, s.value)
print("failures:", r.failures)
Path("out2.json").write_text(report_to_json(r))
```

CSV (`--csv`) is one row per `(run, sample)` for successful runs, plus one
row per failed run carrying the failure verdict. The schema is
`suite, benchmark, run, phase, <variant cols>, metric, value, unit, lower_is_better, failure`
where variant columns are the **union** of every axis observed across all runs
(cells absent in a particular run are blank).

Dir (`--dir`) writes a per-execution tree
(`<suite>/<bench>/<phase>/<run>/{stdout,stderr,exitcode,rusage,seq}`).

### Metric — output → metrics

A Metric parses one `ExecutionResult` into zero or more `Sample`s. The Runner
only calls `process()` on a run it judged successful, so a Metric never has to
re-check exit codes.

```python
from benchr import FloatPerLine, Time, max_rss

.with_metric(
    FloatPerLine("s").last_line().lower_is_better(),  # parse last stdout line as seconds
    max_rss(),                                        # peak RSS from rusage
    Time(user=True),                                  # wall + user CPU
)
```

Built-in metric builders exported from `benchr`: `Time`, `Regex`,
`FloatPerLine`, `Rebench`, `RUsage`, `Constant`, `max_rss()`. See
[`src/benchr/grammar/metric.py`](src/benchr/grammar/metric.py) for the full
list and [`examples/custom_metric.py`](examples/custom_metric.py) to
write your own.

### StoppingPolicy and PolicyState — when to stop

Each benchmark phase (warmup, measure) has a **`StoppingPolicy`** that decides
when enough runs have been collected. The split is deliberate:

* `StoppingPolicy` — frozen, hashable **configuration**.
  `policy.start() → PolicyState`.
* `PolicyState` — mutable **per-run observer** the runner pumps:

  ```python
  state = policy.start()
  while not state.converged():
      run += 1
      samples = execute_and_parse(...)        # [] on failure
      state.observe(run, samples)
  ```

This keeps benchmarks immutable while each *run* of a benchmark gets its own
live state. Built-in policies:

* **`FixedRuns(n)`** — converge after `n` runs (success or failure).
  Bounded, parallel-safe.
* **`CoefficientOfVariation(metric, threshold=0.02, window=5, min_runs=10)`** —
  converge once the rolling stdev/mean of `metric` over the last `window` runs
  drops below `threshold`. Unbounded, sequential.
* **`Custom(state_factory)`** — wrap any `() -> PolicyState`.

Combinators:

| Combinator        | Converged when…           | Sugar                |
|-------------------|---------------------------|----------------------|
| `a & b`           | both converged            |                      |
| `a \| b`          | either converged          |                      |
| `a.at_least(n)`   | `a & FixedRuns(n)`        | floor on runs        |
| `a.at_most(n)`    | `a \| FixedRuns(n)`       | cap on runs          |

```python
# run until CoV stable, but never fewer than 5 or more than 30
CoefficientOfVariation("elapsed", threshold=0.02).at_least(5).at_most(30)
```

The `metric` a CoV policy watches must match what a Metric emits
(`Time()` emits `elapsed`/`user`/`system`; `FloatPerLine()` defaults to
`runtime`). A CoV watching a metric nobody emits never converges and runs to
the `.at_most(n)` cap. See [`examples/convergence.py`](examples/convergence.py),
[`examples/jit_warmup.py`](examples/jit_warmup.py),
[`examples/custom_policy.py`](examples/custom_policy.py).

### Runner

* **`Sequential`** — one benchmark at a time. The default.
* **`Parallel(workers, fanout=False)`** — `n` workers drive one benchmark
  coroutine each. With `fanout=True`, benchmarks whose policies are
  *independent* and *bounded* (e.g. `FixedRuns`) have their individual runs
  spread across workers. Convergence-driven benchmarks stay sequential.
* **`Dry`** — advance each coroutine once and print what *would* run; no
  subprocess. `--dry -v` dumps every field of the `ScheduledExecution`.

```console
$ uv run examples/factory.py --dry -v
factory_demo/tiny #1 [measure]
  suite:      factory_demo
  benchmark:  tiny
  run:        1
  phase:      measure
  command:    python3 -c sum(range(1000))
  cwd:        /tmp
  env:        {}
  timeout:    <none>
  stdin:      <none>
  metrics:    Time
  success:    <default>
  plan:       warmup x0, measure x5
  variant:    {}
  label:      <none>
```

---

## Worked examples

### Matrix — cross-product variants

A benchmark's `.with_matrix(**axes)` declares the axes that vary; the
cartesian product of axis values produces the *variants* of that benchmark.
Variant values reach `with_command` / `with_skip` callables as attributes on
the benchmark (`b.compiler`, `b.opt`).

```python
from benchr import Path, Regex, bench, suite

def cmd(b, ctx):
    return ["sh", "-c", f"echo {b.compiler}-{b.opt}: $((RANDOM%50+50))"]

suite("compile_matrix")
    .add(
        bench("compute")
            .with_command(cmd)
            .with_matrix(compiler=["gcc", "clang"], opt=["O0", "O2"])
    )
    .with_cwd(Path("/tmp"))
    .with_metric(Regex("size", r"(\d+)\s*$", unit="lines"))
    .runs(3)
```

```console
compile_matrix/compute/compiler=gcc, opt=O0: 0|3 runs
  size  (mean ± σ):  60.33 ± 10.02    (50.00 … 70.00)
compile_matrix/compute/compiler=gcc, opt=O2: 0|3 runs
  size  (mean ± σ):  68.00 ± 10.54    (58.00 … 79.00)
...
```

Each cell's `RunRecord.variant` carries the axis values so reporters split by
axis. Ranking in the end-of-run "Summary" compares variants *within* a
benchmark; cross-benchmark ranking is never emitted (different programs are
not directly comparable). See [`examples/matrix.py`](examples/matrix.py).

### Skips: dropping or slicing matrix cells

```python
# Full cartesian minus one cell:
bench("regex").with_matrix(vm=["v8", "jsc"], size=[100, 500])
              .with_skip(vm="v8", size=500)

# Slice (predicate form): keep only jsc
bench("regex").with_matrix(vm=["v8", "jsc"], size=[100, 500])
              .with_skip(lambda b: b.vm != "jsc")
```

`Suite.with_matrix` / `Suite.with_skip` apply the same shape across every
contained benchmark.

### File-discovered benchmarks

```python
from benchr import FloatPerLine, suite

suite("LoxSuite")
    .from_files(lambda ctx: ctx.cwd / "benchmarks", pattern=r"\.lox$")
    .with_command(lambda b, ctx: [str(ctx.lox), str(b.path)])
    .runs(10)
    .with_metric(FloatPerLine("s").last_line().lower_is_better())
```

`b.path` is set on each discovered benchmark; access any user data via
`benchmark.<attr>`. See [`examples/external/lox.py`](examples/external/lox.py),
[`examples/external/rcp.py`](examples/external/rcp.py).

### Failure handling

```python
from benchr import Path, Time, bench, suite

suite("flaky",
    bench("ok").with_command(["sh", "-c", "sleep 0.02"]).runs(3),
    bench("broken").with_command(["sh", "-c", "exit 7"]).runs(3),
).with_cwd(Path("/tmp")).with_metric(Time())
```

```console
flaky/ok: 0|3 runs
  elapsed  (mean ± σ):  25.60 ± 1.61    (23.77 … 26.77)

flaky/broken: 3|0 runs

Failures:
  ✗ flaky/broken #1  — exit 7: (no output)
  ✗ flaky/broken #2  — exit 7: (no output)
  ✗ flaky/broken #3  — exit 7: (no output)
```

### Baseline comparison

```console
$ python compare_baseline.py --json baseline.json
$ python compare_baseline.py --compare baseline.json
cmp/fast:
  runs:
    baseline: 0|5 (failed|succeeded)
    current:  0|5 (failed|succeeded)
  elapsed:
    current was 1.12 ± 0.12 times worse than baseline
...
Summary (geometric mean of ratios):
  cmp:
    elapsed:
      current was 1.06 ± 0.06 times worse than baseline
```

### Programmatic use

```python
from benchr import Path, Sequential, Time, bench, suite

s = (suite("prog", bench("a").with_command(["sleep", "0.02"]))
     .with_cwd(Path("/tmp")).with_metric(Time()).runs(3))

report = Sequential().run([s], ctx=None)
for run in report.runs:
    for sample in run.samples:
        print(run.benchmark, run.run, sample.metric, sample.value)
print("failures:", report.failures)
```

---

## User parameters (`ctx`)

A script declares CLI flags as a `@dataclass`. `run(..., params=Cls)` builds
argparse flags from the field annotations and passes a typed instance to every
builder lambda as `ctx`.

```python
from dataclasses import dataclass
from benchr import Path, Time, bench, run, suite

@dataclass
class Params:
    binary: Path             # required  -> --binary PATH
    size: int = 100          # optional  -> --size INT   (default: 100)

def cmd(b, ctx: Params):
    return [str(ctx.binary), str(ctx.size)]

run(
    suite("s", bench("x")).with_cwd(".").with_command(cmd).with_metric(Time()),
    params=Params,
)
```

Supported field types: `str`, `int`, `float`, `bool` (→ `--flag/--no-flag`),
`Path`, `Optional[T]`.

---

## CLI reference

```
benchr bench   [--runs N] [--warmup N] [--timeout T] [--jobs J] [--dry] [-v]
               [--json F] [--csv F] [--dir D] [--compare base.json ...]
               [--metric M] CMD1 CMD2 ...

benchr compare a.json b.json ...   [--metric m1,m2]   # first file = baseline
benchr show    out.json            [--metric m]
```

A benchmark script built with `run(...)` accepts the same benchr flags plus its
own `@dataclass` flags:

```
python my_bench.py [--<user params>] [--runs N] [--warmup N] [--jobs J]
                   [--quiet] [--dry] [-v] [--json F] [--csv F] [--dir D]
                   [--compare base.json ...]
```

---

## Module layout

```
src/benchr/
    __init__.py            public re-exports
    cli.py                 run() entry point + bench/compare/show
    grammar/
        execution.py       Execution, ExecutionResult, ScheduledExecution,
                           Variant, TIMEOUT_RC, SPAWN_FAIL_RC
        metric.py          Metric + combinators + Time/Regex/FloatPerLine/…
        policy.py          StoppingPolicy, FixedRuns, CoV, combinators
        benchmark.py       Benchmark + compile() coroutine
        suite.py           Suite + matrix + from_files
        context.py         @dataclass -> argparse glue
    runner/
        base.py            execute(), plan(), Runner base + coroutine pump
        sequential.py / parallel.py / dry.py
    report/
        sample.py          Sample, RunRecord, Report, JSON round-trip
        stats.py           grouping, stats, ratios, geomean
        formatter.py       DefaultSummary, Compact
        reporter.py        streaming sinks (CompositeReporter, Csv, Json, …)
        theme.py           rich theme + shared Console
```

---

## Development

```console
uv run pytest          # 136 tests
uv run benchr bench --runs 20 'sleep 0.1' 'sleep 0.2'
```
