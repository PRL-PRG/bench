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
[1|12] bench/sleep 0.05 #1 ok
[2|12] bench/sleep 0.05 #2 ok
[3|12] bench/sleep 0.05 #3 ok
...
[12|12] bench/sleep 0.1 #6 ok

bench/sleep 0.05: 0|5 runs
  elapsed [ms] (mean ± σ):  55.22 ± 2.11    (51.83 … 57.35)

bench/sleep 0.1: 0|5 runs
  elapsed [ms] (mean ± σ):  106.79 ± 2.45    (103.94 … 109.83)

Summary
  'sleep 0.05' [elapsed] was
    1.92 ± 0.08 times lower than 'sleep 0.1'
```

`0|5 runs` means **0 failures | 5 successes**. `elapsed` is auto-scaled to
milliseconds from the seconds the `Time()` metric emits.

### As a script

```python
from benchr import Time, bench, run, suite

s = (
    suite("demo",
        bench("fast").with_command(["sleep", "0.05"]),
        bench("slow").with_command(["sleep", "0.1"]),
    )
    .with_metric(Time())   # measure wall-clock elapsed
    .with_runs(5)               # 5 measured runs each
)

if __name__ == "__main__":
    run(s)
```

`cwd` defaults to the invoking directory; metrics default to `Time()` — both
are suite defaults, shown explicitly elsewhere.

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
(`Suite`, `Benchmark` — the builder grammar in `benchr.grammar`) and the things
a run produces (`Run`, `Observation`, `Sample`, `Report` — pure data in
`benchr.core`, so Reports round-trip cleanly through JSON). Everything except
the final reporting lives in `benchr.core`; see [Module layout](#module-layout)
for the one-line layering rule.

* `Suite`: a named collection of benchmarks plus the defaults they inherit
* `Benchmark`: command + cwd + env + metrics + stopping policy + variants
* `Run`: one process execution — identity + outcome + its `Observation`s
* `Observation`: one measurement point — a bag of `Sample`s (+ optional failure)
* `Sample`: one metric value
* `Report`: `[Run]` + per-variant warmup counts, JSON round-trippable

### Pipeline

```
   plan(suites, params)       ← Suite.materialize → BenchmarkSpec.create:
        │                        resolve the matrix + fill every UNSET field
        ▼
   [Benchmark]                ← fully-resolved variants (Execution + metrics + policies)
        │                        a Runner drives one Controller per Benchmark
        ▼
   benchmarking_loop(warmup, runs)   ← yields in_warmup per slot until both policies converge
        │                        the Controller pulls one Observation per slot from a RunSource
        ▼
   RunSource.next()
     ├─ CommandSource  → execute() spawns one process per observation
     └─ HarnessSource  → one process; a monitor frames its output into observations
        │
        ▼
   ExecutionResult            ← stdout, stderr, returncode, runtime, rusage
        │                        Benchmark.success(result) → Verdict (None = ok, str = failure)
   ┌────┴────────┐
   ▼             ▼
 failure       success → Metric.process() → [Sample]
   │             │
   └─────┬───────┘
         ▼
   Observation(samples, failure, label)
         │                        the source assembles observations into Run(s) on close()
         ▼
   Run(identity, outcome, [Observation])   ← command: 1 observation; harness: N
         │                        Controller → StoppingPolicy.observe(obs) + Report.add(run)
         ▼ not converged → next slot
         ▼ converged
     Report  →  Reporter (stream) + Formatter (final summary)
```

A *harness* benchmark (`.with_harness()`, see below) takes a shortcut through
the same pipeline: one long-running process, then a monitor frames its output
into many `Observation`s — all belonging to a single `Run`.

A **failed run still produces a `Run`** (with `failure` set and an empty
`Observation`). That's why a failing benchmark appears in the summary as
`3|0 runs` (3 failures, 0 successes) with the reason listed in a `Failures:`
block — no fake metrics poisoning the stats. `.with_runs(N)` means "N
attempts", so a crashing benchmark stops after N runs rather than retrying.

### Suite vs Benchmark — who overrides whom

A `Suite` *stores* defaults (command, cwd, env, metrics, policies, …) next to
its benchmarks; nothing propagates when a `.with_*` method is called. Every
unset benchmark field holds the `UNSET` null object, meaning "inherit the
suite's value", and resolution happens once, at materialize time. Per-benchmark values always win over suite defaults — and
because resolution is deferred, **builder-call order never matters**:

```python
suite("S",
    bench("a").with_timeout(5),       # a keeps timeout=5
    bench("b"),                       # b gets timeout=30 from the suite
).with_timeout(30)                    # same result if called before add()
```

One method deviates from plain inherit-if-unset: `with_env` **merges** (suite
env first, benchmark keys win). Metrics don't merge — `Suite.with_metric` and
`Benchmark.with_metric` both **set** (replace), and a benchmark with its own
metrics ignores the suite default (initially `Time()`) entirely.

The CLI sits one layer above that: `--runs N` and `--warmup N` are *forcing*
overrides — they beat every benchmark's value regardless of what the script
set. The full precedence, most specific wins:

```
   CLI --runs 10                     ← forcing override, beats everything
        ▲
   bench(...).with_runs(10)               ← per-benchmark explicit value
        ▲
   with_matrix(command=[...])        ← benchmark matrix-dimension default (command/cwd/env dims)
        ▲
   suite(...).with_runs(10)               ← suite default, fills unset benchmarks
```

Other CLI flags (`--json`, `--csv`, `--dir`, `--compare`) are **additive**: they
attach extra output sinks alongside whatever reporter the script configured.

### Run, Observation, and Sample

The runner produces `Run`s. Each `Run` carries identity + outcome + a list of
`Observation`s, and each `Observation` holds the `Sample`s measured at one
point:

```python
Run(
    suite, benchmark, variant, variant_label, run,   # identity
    command, cwd, returncode, runtime, failure, message,  # outcome
    observations: list[Observation],                 # command: 1; harness: N
)

Observation(
    samples: list[Sample],     # parsed metrics (empty on failure)
    failure,                   # None, or the reason this observation failed
    label,                     # display identifier, for live progress
)

Sample(metric, value, unit, lower_is_better)         # metric data only
```

* `variant` is a sorted tuple of `(dimension, value)` pairs identifying the
  matrix cell (e.g. `(("compiler","gcc"),("opt","O2"))`). Empty tuple when
  the benchmark has no matrix.
* `variant_label` is the human-readable label of the variant (from
  `Benchmark.with_label(...)`, or the formatted `variant` tuple otherwise).
* observation numbers are **continuous**: a variant's warmup observations come
  first, measured ones follow. Records carry no warmup marking — `Report.warmups`
  maps each benchmark variant to its warmup count once, and stats drop those
  leading observations. Every run appears in JSON/CSV/dir outputs.
* `Sample.lower_is_better` is set by the Metric — `True` for runtime, `False`
  for throughput, `None` when not comparable.

A command benchmark yields one `Run` with a single `Observation`; a harness
yields one `Run` with many. A *successful* observation carries one or more
`Sample`s (one per metric emitted); a failed one carries no samples and a
non-None `failure`.

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
      "variant_label": "",
      "run": 1,
      "command": ["python3", "-c", "sum(range(1000))"],
      "cwd": "/home/user/benchr",
      "returncode": 0,
      "runtime": 0.014422666048631072,
      "failure": null,
      "message": "",
      "observations": [
        {
          "samples": [
            {
              "metric": "elapsed",
              "value": 0.014422666048631072,
              "unit": "s",
              "lower_is_better": true
            }
          ],
          "failure": null,
          "label": "tiny #1"
        }
      ]
    }
  ],
  "warmups": {}
}
```

Each `Run` carries its `observations` (a command benchmark has exactly one per
run, a harness many). A run's `stdout`/`stderr`/`env` are kept in memory for
live reporters but excluded from JSON by default — they bloat the file and are
rarely needed offline — which is why they don't appear above (pass
`include_output=True` to `report_to_json` to keep them).

`warmups` maps a benchmark-variant key to the number of its leading
*observations* that were warmup (only non-zero entries; here none) — that is
how a reloaded report, e.g. a `--compare` baseline, knows which observations
the stats must drop.

Programmatically:

```python
from benchr import report_from_json, report_to_json

r = report_from_json(Path("out.json").read_text())
for run in r.runs:
    for obs in run.observations:
        for s in obs.samples:
            print(run.benchmark, run.run, s.metric, s.value)
print("failures:", r.failures)
Path("out2.json").write_text(report_to_json(r))
```

CSV (`--csv`) is one row per `Sample` for successful observations, plus one
row per failed observation/run carrying the failure verdict. The schema is
`suite, benchmark, run, <variant cols>, metric, value, unit, lower_is_better, failure`
where variant columns are the **union** of every matrix dimension observed across all runs
(cells absent in a particular run are blank). All runs appear, warmup
included — to drop warmup in external analysis, read `warmups` from the JSON.

DirReporter (`--dir`) writes a per-execution tree
(`<suite>/<bench>/<n>/{stdout,stderr,exitcode,seq}`).

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
`FloatPerLine`, `Rebench`, `RUsage`, `max_rss()`. See
[`src/benchr/core/metric.py`](src/benchr/core/metric.py) for the full
list and [`examples/custom_metric.py`](examples/custom_metric.py) to
write your own.

### StoppingPolicy and PolicyState — when to stop

Warmup and measurement each have a **`StoppingPolicy`** that decides when
enough runs have been collected. The split is deliberate:

* `StoppingPolicy` — frozen, hashable **configuration**.
  `policy.start() → PolicyState`.
* `PolicyState` — mutable **per-run observer** the runner pumps:

  ```python
  state = policy.start()
  while not state.satisfied():
      obs = observe_one(...)        # an Observation (empty samples on failure)
      state.observe(obs)
  ```

This keeps benchmarks immutable while each *run* of a benchmark gets its own
live state. Built-in policies:

* **`FixedRuns(n)`** — converge after `n` observations (success or failure).
  Bounded, parallel-safe.
* **`CoefficientOfVariation(metric, threshold=0.02, window=5, min_runs=10)`** —
  converge once the rolling stdev/mean of `metric` over the last `window` runs
  drops below `threshold`. Unbounded, sequential.
* **Custom** — subclass `StoppingPolicy` (the frozen config, returns a
  `PolicyState` from `start()`) and `PolicyState` (the per-observation
  observer); see [`examples/custom_policy.py`](examples/custom_policy.py).

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

`min_runs` counts runs that produced the watched metric; `.at_least(n)` counts
all runs including failures.

The `metric` a CoV policy watches must match what a Metric emits
(`Time()` emits `elapsed`/`user`/`system`; `FloatPerLine()` defaults to
`runtime`). A CoV watching a metric nobody emits never converges and runs to
the `.at_most(n)` cap. See [`examples/convergence.py`](examples/convergence.py),
[`examples/jit_warmup.py`](examples/jit_warmup.py),
[`examples/custom_policy.py`](examples/custom_policy.py).

### Runner

* **`Sequential`** — one benchmark at a time, runs in order. The default, and
  the only sound choice when wall-clock time is the metric (no contention).
* **`Parallel(workers)`** — runs up to `workers` benchmarks concurrently, one
  `Controller` per benchmark (per-*benchmark* parallelism, not per-run). Each
  benchmark still drives its own sequential feedback loop, so any policy —
  fixed, convergence-driven, or order-dependent — works unchanged. This is the
  one sound use of parallelism in a benchmark tool: wall-clock timing under
  contention is meaningless, so use it only for work where time is *not* the
  metric — test suites (pass/fail), smoke runs, or getting through a batch
  faster. `--jobs N` bounds how many benchmarks run at once.
* **`Dry`** — enumerate and print what *would* run; no subprocess.
  `--dry -v` dumps every field of the resolved `Benchmark`/`Execution`.

Harness benchmarks run in all three: `Parallel` treats one as a single work
item (one process), so harness benchmarks and variants fan out across
workers; `Dry` prints one `[harness]`-marked line.

A runner executes a flat list of planned benchmarks, not suites: call
`plan(suites, ctx)` to materialize first, then `runner.run(planned, ctx)`. The
`run(...)` entry point does this for you (and applies `--runs`/`--warmup`).

```console
$ uv run examples/factory.py --dry -v
factory_demo/tiny #1
  suite:      factory_demo
  benchmark:  tiny
  run:        1
  command:    python3 -c sum(range(1000))
  cwd:        /home/user/benchr
  env:        {}
  timeout:    <none>
  stdin:      <none>
  metrics:    Time
  success:    <default>
  variant:    {}
  label:      <none>
```

---

## Worked examples

### Matrix — cross-product variants

A benchmark's `.with_matrix(**dims)` declares the dimensions that vary; the
cartesian product of dimension values produces the *variants* of that benchmark.
Variant values reach `with_command` / `with_cwd` / `with_env` callables via
`ctx.matrix` (`ctx.matrix.compiler`, `ctx.matrix.opt`); `add_matrix_skip` /
`with_label` callables instead receive the variant-stamped benchmark and read
them as attributes (`b.compiler`, `b.opt`).

```python
from benchr import Regex, bench, suite

def cmd(ctx):
    return ["sh", "-c", f"echo {ctx.matrix.compiler}-{ctx.matrix.opt}: $((RANDOM%50+50))"]

suite("compile_matrix")
    .add(
        bench("compute")
            .with_command(cmd)
            .with_matrix(compiler=["gcc", "clang"], opt=["O0", "O2"])
    )
    .with_metric(Regex("size", r"(\d+)\s*$", unit="lines"))
    .with_runs(3)
```

```console
compile_matrix/compute/compiler=gcc, opt=O0: 0|3 runs
  size  (mean ± σ):  60.33 ± 10.02    (50.00 … 70.00)
compile_matrix/compute/compiler=gcc, opt=O2: 0|3 runs
  size  (mean ± σ):  68.00 ± 10.54    (58.00 … 79.00)
...
```

Each cell's `Run.variant` carries the dimension values so reporters split by
dimension. Ranking in the end-of-run "Summary" compares variants *within* a
benchmark; cross-benchmark ranking is never emitted (different programs are
not directly comparable). See [`examples/matrix.py`](examples/matrix.py).

### Skips: dropping or slicing matrix cells

```python
# Full cartesian minus one cell:
bench("regex").with_matrix(vm=["v8", "jsc"], size=[100, 500])
              .add_matrix_skip(vm="v8", size=500)

# Slice (predicate form): keep only jsc
bench("regex").with_matrix(vm=["v8", "jsc"], size=[100, 500])
              .add_matrix_skip(lambda b: b.vm != "jsc")
```

`Suite.with_matrix` / `Suite.add_matrix_skip` apply the same shape across every
contained benchmark. See [`examples/matrix_skips.py`](examples/matrix_skips.py).

### File-discovered benchmarks

`from_files(root, pattern=...)` returns a `list[Benchmark]`. Splat a fixed root
straight into `suite(name, *from_files(...))`, or wrap it in `.factory(...)` when
the root depends on `ctx` (resolved after CLI parsing):

```python
from benchr import FloatPerLine, from_files, suite

suite("LoxSuite")
    .factory(lambda ctx: from_files(ctx.params.root / "benchmarks", pattern=r"\.lox$"))
    .with_command(lambda ctx: [str(ctx.params.lox), str(ctx.matrix.path)])
    .with_runs(10)
    .with_metric(FloatPerLine("s").last_line().lower_is_better())
```

`path` is set on each discovered benchmark: read it as `ctx.matrix.path` in
command/cwd/env callables (or `b.path` on the benchmark itself in skip/label
callables). See [`examples/discovery.py`](examples/discovery.py),
[`examples/external/lox.py`](examples/external/lox.py),
[`examples/external/rcp.py`](examples/external/rcp.py).

### Harness benchmarks — one process runs all iterations

Benchmarking a VM, you don't re-execute it per run: you start it once and let
it iterate internally so the JIT can warm up. `.with_harness()` marks a
benchmark (or, on a `Suite`, every benchmark) as such a *harness*: the command
is executed **once**, the metrics parse the complete output (one sample per
iteration), and each iteration becomes one run record — the first
`warmup.max_runs()` of them discarded by the stats like any other warmup.
The harness must be told how many iterations to run; derive the count in the
command fn from the policies:

```python
def vm_command(ctx):
    n = ctx.warmup.max_runs() + ctx.runs.max_runs()
    return [str(ctx.params.vm), str(ctx.matrix.path), "-n", str(n)]

suite("vm", *from_files("benchmarks", pattern=r"\.lox$"))
    .with_command(vm_command)
    .with_metric(FloatPerLine("ms").lower_is_better())  # one time per line
    .with_warmup(5)
    .with_runs(10)
    .with_harness()
```

Real harnesses fit the same shape: Renaissance (`-r N` plus a `Regex` on its
`iteration N completed (… ms)` lines), LevelDB's `db_bench` (a `Regex` on
`micros/op`, warmup 0), or any ReBench-format harness (the `Rebench()`
metric). See [`examples/harness.py`](examples/harness.py).

The contract, spelled out:

* warmup/runs must be **bounded** counts (no `CoefficientOfVariation`) — the
  runner cannot stop a harness mid-flight, so the policies are rejected at
  materialize time otherwise. `--runs`/`--warmup` overrides work and reach
  the command fn.
* the output is parsed only after the process exits (no live streaming, no
  live progress), and `with_timeout` covers the whole process — all
  iterations, not one.
* with several metrics, the i-th sample of each metric belongs to iteration
  i. A harness that produces **no** samples, or fewer iterations than
  `warmup + runs`, is reported as a failure (a `Time()`-only harness
  benchmark fails loudly instead of producing an empty summary — set an
  output-parsing metric).
* per-iteration records share the single execution's outcome: the process
  `runtime` repeats on every record in the JSON, and `--dir` writes the full
  stdout per iteration directory. The iteration measurements are whatever the
  harness self-reports — there is no per-iteration rusage.

### Failure handling

```python
from benchr import Time, bench, suite

suite("flaky",
    bench("ok").with_command(["sh", "-c", "sleep 0.02"]).with_runs(3),
    bench("broken").with_command(["sh", "-c", "exit 7"]).with_runs(3),
).with_metric(Time())
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
from benchr import Sequential, Time, bench, plan, suite

s = (suite("prog", bench("a").with_command(["sleep", "0.02"]))
     .with_metric(Time()).with_runs(3))

report = Sequential().run(plan([s], None), None)
for run in report.runs:
    for obs in run.observations:
        for sample in obs.samples:
            print(run.benchmark, run.run, sample.metric, sample.value)
print("failures:", report.failures)
```

---

## User parameters (`ctx`)

A script declares CLI flags as a `@dataclass`. `run(..., params=Cls)` builds
argparse flags from the field annotations and exposes the typed instance to
every builder callable as `ctx.params` (on the `Context` passed as the single
argument).

```python
from dataclasses import dataclass
from pathlib import Path
from benchr import Context, Time, bench, run, suite

@dataclass
class Params:
    binary: Path             # required  -> --binary PATH
    size: int = 100          # optional  -> --size INT   (default: 100)

def cmd(ctx: Context[Params]):
    return [str(ctx.params.binary), str(ctx.params.size)]

run(
    suite("s", bench("x")).with_command(cmd).with_metric(Time()),
    params=Params,
)
```

See [`examples/params.py`](examples/params.py).

Supported field types: `str`, `int`, `float`, `bool` (→ `--flag/--no-flag`),
`Path`, `Optional[T]`.

---

## CLI reference

```
benchr bench   [--runs N] [--warmup N] [--timeout T] [--jobs J] [--no-progress] [--dry] [-v]
               [--json F] [--csv F] [--dir D] [--compare base.json ...]
               [--metric M] CMD1 CMD2 ...

benchr compare a.json b.json ...   [--metric m1,m2]   # first file = baseline;
                                                       # a single file just summarizes
```

A benchmark script built with `run(...)` accepts the same benchr flags plus its
own `@dataclass` flags:

```
python my_bench.py [--<user params>] [--runs N] [--warmup N] [--jobs J]
                   [--no-progress] [--dry] [-v] [--json F] [--csv F] [--dir D]
                   [--compare base.json ...]
```

---

## Module layout

Layering rule: `core ← grammar ← report ← runner ← cli` — every import
points left.

```
src/benchr/
    __init__.py            public re-exports
    cli.py                 run() entry point + bench/compare
    core/                  pure mechanism + pure data; imports nothing from benchr
        execution.py       Execution, ExecutionResult, Verdict,
                           Variant, TIMEOUT_RC, SPAWN_FAIL_RC
        process.py         execute() + spawn_streaming() + SIGINT machinery
        metric.py          Metric + combinators + Time/Regex/FloatPerLine/…
        policy.py          StoppingPolicy, FixedRuns, CoV, combinators
        loop.py            benchmarking_loop — the pure feedback core
        sample.py          Sample, Observation, Run, Report, JSON round-trip
    grammar/               builder sugar on top of core
        benchmark.py       BenchmarkSpec (template) + Benchmark (resolved), UNSET
        suite.py           Suite + matrix + from_files
        context.py         @dataclass -> argparse glue
    report/
        stats.py           grouping, stats, ratios, geomean
        formatter.py       DefaultSummary, Compact
        reporter.py        streaming sinks (CompositeReporter, CsvReporter, JsonReporter, …)
        theme.py           rich theme + shared Console
    runner/
        base.py            plan(), Runner base, the verbose plan dump
        controller.py      the per-benchmark feedback loop driver
        source.py          CommandSource / HarnessSource (spawn + assemble Runs)
        sequential.py / parallel.py / dry.py
```

---

## Development

```console
uv run pytest
uv run benchr bench --runs 20 'sleep 0.1' 'sleep 0.2'
```
