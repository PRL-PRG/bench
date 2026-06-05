# benchr

A lightweight Python benchmarking framework.

Two ways to use it:

* **`benchr bench`** — hyperfine-style CLI for ad-hoc command timing.
* **`run(suite, …)`** — declarative Python scripts for repeatable benchmark
  configurations (file-discovered benchmarks, matrices, custom processors,
  convergence policies).

---

## Quick start

### As a CLI

```console
$ benchr bench --runs 5 --warmup 1 'sleep 0.05' 'sleep 0.1'
[1/12] bench/sleep 0.05 #1  ok       ← warmup
[2/12] bench/sleep 0.05 #1  ok       ← measure starts
[3/12] bench/sleep 0.05 #2  ok
...
[12/12] bench/sleep 0.1 #5  ok

bench/sleep 0.05: 0/5 runs
  elapsed  (mean ± σ):  55.71 ± 1.98    (53.64 … 58.14)

bench/sleep 0.1: 0/5 runs
  elapsed  (mean ± σ):  108.05 ± 1.58    (105.39 … 109.39)

Summary
  'sleep 0.05' ran
    1.97 ± 0.08 times faster than 'sleep 0.1'
```

`0/5 runs` means **0 failures / 5 successes**. The `elapsed` numbers are
milliseconds (auto-scaled from the seconds the `P.time()` processor emits).

### As a script

```python
from benchr import P, Path, bench, run, suite

s = (
    suite("demo",
        bench("fast").with_command(["sleep", "0.05"]),
        bench("slow").with_command(["sleep", "0.1"]),
    )
    .with_cwd(Path("."))
    .with_process(P.time())   # measure wall-clock elapsed
    .runs(5)                  # 5 measured runs each
)

if __name__ == "__main__":
    run(s)
```

```console
$ python demo.py --runs 10 --json out.json
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
(`Suite`, `Benchmark`) and the things a run produces (`Sample`, `RunRecord`,
`Report`). Everything in the right column is **pure data** — Reports
round-trip cleanly through JSON.

```
  WHAT YOU BUILD                       WHAT A RUN PRODUCES
  -------------                        --------------------
  Suite     a named bag of             Sample      one measurement
              Benchmarks +                          (metric, value, unit, run, phase)
              propagating defaults
                                       RunRecord   one execution
  Benchmark command + cwd + env +                   (command, returncode, failure, …)
              Processor(s) +
              StoppingPolicy(s)        Report      [Sample] + [RunRecord] + metadata
                                                    (JSON round-trippable)
```

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
   │             │ Processor.process()
   │             ▼
 RunRecord    Sample (+ RunRecord)
   │             │
   │             ▼
   │      StoppingPolicy.observe(run, samples)
   │             │
   │             ▼ not converged → next ScheduledExecution
   └─────────────┘
                  │ converged
                  ▼
              Report  →  Reporter (stream) + Formatter (final summary)
```

A **failed run still produces a `RunRecord`** (with `failure` set, no `Sample`s).
That's why a failing benchmark appears in the summary as `3/0 runs` (3 failures,
0 successes) with the reason listed in a `Failures:` block — no fake metrics
poisoning the stats. `.runs(N)` means "N attempts", so a crashing benchmark
stops after N runs rather than retrying.

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

### Sample and RunRecord

The two pieces of pure data the runner produces:

```python
Sample(suite, benchmark, variant, run, phase,
       metric, value, unit, lower_is_better, variant_label)
RunRecord(suite, benchmark, variant, run, phase, command,
          returncode, runtime, failure, message, variant_label)
```

* `variant` is a sorted tuple of `(axis, value)` pairs identifying the
  matrix cell (e.g. `(("compiler","gcc"),("opt","O2"))`). Empty tuple when
  the benchmark has no matrix.
* `variant_label` is the human-readable label of the variant (from
  `Benchmark.with_label(...)`, or the formatted `variant` tuple otherwise).
* `phase` is `"warmup"` or `"measure"`. Warmup samples are reported in
  JSON/CSV/dir outputs but excluded from stats.
* `lower_is_better` is set by the Processor — `True` for runtime, `False` for
  throughput, `None` when not comparable.

Every execution yields one `RunRecord`. A *successful* run also yields one or
more `Sample`s (one per metric the Processor emitted).

### Serialization

`Report` round-trips through JSON. Run any script with `--json out.json` and you
get:

```json
{
  "metadata": {},
  "samples": [
    {
      "suite": "factory_demo",
      "benchmark": "tiny",
      "variant": [],
      "run": 1,
      "phase": "measure",
      "metric": "elapsed",
      "value": 0.014422666048631072,
      "unit": "s",
      "lower_is_better": true
    }
  ],
  "runs": [
    {
      "suite": "factory_demo",
      "benchmark": "tiny",
      "variant": [],
      "run": 1,
      "phase": "measure",
      "command": ["python3", "-c", "sum(range(1000))"],
      "returncode": 0,
      "runtime": 0.014422666048631072
    }
  ]
}
```

Programmatically:

```python
from benchr import report_from_json, report_to_json

r = report_from_json(Path("out.json").read_text())
print(r.samples[0].value, r.failures)
Path("out2.json").write_text(report_to_json(r))
```

CSV (`--csv`) is sample-flat; dir (`--dir`) writes a per-execution tree
(`<suite>/<bench>/<phase>/<run>/{stdout,stderr,exitcode,rusage,seq}`).

### Processor — output → metrics

A Processor parses one `ExecutionResult` into zero or more `PartialSample`s
(the runner stamps benchmark identity onto them to make full `Sample`s). The
Runner only calls `process()` on a run it judged successful, so a Processor
never has to re-check exit codes.

```python
.with_process(
    P.float_per_line("s").last_line().lower_is_better(),  # parse last stdout line as seconds
    P.max_rss(),                                          # peak RSS from rusage
    P.time(user=True),                                    # wall + user CPU
)
```

Built-ins in the `P.` namespace: `P.time`, `P.max_rss`, `P.rusage`,
`P.float_per_line`, `P.regex`, `P.rebench`, `P.constant`. See
[`src/benchr/grammar/processor.py`](src/benchr/grammar/processor.py) for the
full list and [`examples/custom_processor.py`](examples/custom_processor.py)
to write your own.

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

The `metric` a CoV policy watches must match what a Processor emits
(`P.time()` emits `elapsed`/`user`/`system`; `P.float_per_line()` defaults to
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
  subprocess. `--dry -v` adds resolved cwd, env, run plan, and matrix variant per
  cell.

```console
$ uv run examples/factory.py --dry -v
factory_demo/tiny
  command: python3 -c sum(range(1000))
  cwd:     /tmp
  plan:    measure x5
factory_demo/small
  command: python3 -c sum(range(100000))
  cwd:     /tmp
  plan:    measure x5
```

---

## Worked examples

### Matrix — cross-product variants

A benchmark's `.with_matrix(**axes)` declares the axes that vary; the
cartesian product of axis values produces the *variants* of that benchmark.
Variant values reach `with_command` / `with_skip` callables as attributes on
the benchmark (`b.compiler`, `b.opt`).

```python
def cmd(b, ctx):
    return ["sh", "-c", f"echo {b.compiler}-{b.opt}: $((RANDOM%50+50))"]

suite("compile_matrix")
    .add(
        bench("compute")
            .with_command(cmd)
            .with_matrix(compiler=["gcc", "clang"], opt=["O0", "O2"])
    )
    .with_cwd(Path("/tmp"))
    .with_process(P.regex("size", r"(\d+)\s*$", unit="lines"))
    .runs(3)
```

```console
compile_matrix/compute/compiler=gcc, opt=O0: 0/3 runs
  size  (mean ± σ):  60.33 ± 10.02    (50.00 … 70.00)
compile_matrix/compute/compiler=gcc, opt=O2: 0/3 runs
  size  (mean ± σ):  68.00 ± 10.54    (58.00 … 79.00)
...
```

Each cell's `Sample.variant` carries the axis values so reporters split by
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
suite("LoxSuite")
    .from_files(lambda ctx: ctx.cwd / "benchmarks", pattern=r"\.lox$")
    .with_command(lambda b, ctx: [str(ctx.lox), str(b.path)])
    .runs(10)
    .with_process(P.float_per_line("s").last_line().lower_is_better())
```

`b.path` is set on each discovered benchmark; access any user data via
`benchmark.<attr>`. See [`examples/external/lox.py`](examples/external/lox.py),
[`examples/external/rcp.py`](examples/external/rcp.py).

### Failure handling

```python
suite("flaky",
    bench("ok").with_command(["sh", "-c", "sleep 0.02"]).runs(3),
    bench("broken").with_command(["sh", "-c", "exit 7"]).runs(3),
).with_cwd(Path("/tmp")).with_process(P.time())
```

```console
flaky/ok: 0/3 runs
  elapsed  (mean ± σ):  25.60 ± 1.61    (23.77 … 26.77)

flaky/broken: 3/0 runs

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
    baseline: 0 failed / 5 succeeded
    current: 0 failed / 5 succeeded
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
from benchr import P, Path, Sequential, bench, suite

s = (suite("prog", bench("a").with_command(["sleep", "0.02"]))
     .with_cwd(Path("/tmp")).with_process(P.time()).runs(3))

report = Sequential().run([s], ctx=None)
for sample in report.samples:
    print(sample.benchmark, sample.run, sample.value)
print("failures:", report.failures)
```

---

## User parameters (`ctx`)

A script declares CLI flags as a `@dataclass`. `run(..., params=Cls)` builds
argparse flags from the field annotations and passes a typed instance to every
builder lambda as `ctx`.

```python
from dataclasses import dataclass
from benchr import Path, run, suite, bench, P

@dataclass
class Params:
    binary: Path             # required  -> --binary PATH
    size: int = 100          # optional  -> --size INT   (default: 100)

def cmd(b, ctx: Params):
    return [str(ctx.binary), str(ctx.size)]

run(
    suite("s", bench("x")).with_cwd(".").with_command(cmd).with_process(P.time()),
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
        execution.py       Execution, ExecutionResult, ScheduledExecution
        processor.py       Processor + combinators + P. builtins
        policy.py          StoppingPolicy, FixedRuns, CoV, combinators
        benchmark.py       Benchmark + compile() coroutine
        suite.py           Suite + matrix + from_files
        context.py         @dataclass -> argparse glue
    runner/
        base.py            execute(), plan(), Runner base + coroutine pump
        sequential.py / parallel.py / dry.py
    report/
        sample.py          Sample, Report, JSON round-trip
        stats.py           grouping, stats, ratios, geomean
        formatter.py       DefaultSummary, Compact
        reporter.py        streaming sinks + rich theme
```

---

## Development

```console
uv run pytest          # 137 tests
uv run benchr bench --runs 20 'sleep 0.1' 'sleep 0.2'
```
