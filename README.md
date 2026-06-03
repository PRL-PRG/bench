# benchr

A lightweight Python benchmarking framework built around a small **algebra of
benchmarks**: a handful of immutable value types that compose with operators
(`|`, `&`), so that easy benchmarks stay one-liners and hard ones remain
expressible. Inspired by hyperfine on the CLI side and dplyr/ggplot on the
fluent-builder side.

```python
from benchr import suite, bench, P, run

s = (
    suite("demo",
        bench("fast").with_command(["sleep", "0.1"]),
        bench("slow").with_command(["sleep", "0.2"]),
    )
    .with_cwd(".")
    .with_process(P.time())   # measure wall-clock elapsed
    .runs(10)                 # 10 measured runs each
)

if __name__ == "__main__":
    run(s)
```

```console
benchr bench --runs 20 --warmup 2 'sleep 0.1' 'sleep 0.2'
```

---

## The benchmark algebra

Everything in benchr is one of nine value types. Two views help: **what the
types are** (the data model) and **how a run flows through them** (the pipeline).

### The value types

```
  What you build
  --------------
    Suite  ---- contains ---->  Benchmark
    (propagates defaults)          |  each Benchmark bundles:
                                   +-- command / cwd / env / timeout
                                   +-- Processor        ExecutionResult -> [Sample]
                                   +-- StoppingPolicy    when to stop (warmup + measure)

  What a run produces  (pure data, JSON round-trippable)
  --------------------
    ExecutionResult = Successful | Failed     one outcome of one Execution
    Sample                                  one parsed metric
    FailureRecord                           one failed run (exit code + diagnostic)
    Report  =  [Sample]  +  [FailureRecord]  +  metadata

  How it renders
  --------------
    Runner      Sequential | Parallel | Dry          executes a list of Suites
    Reporter    streaming sink: Csv, Json, Dir, Table, Progress, Summary, Mixed
    Formatter   Report -> str: DefaultSummary, Compact
```

Two of these types are an *algebra* — they compose with operators:

```
    Processor        p1 | p2                 run both, concatenate the metrics
    StoppingPolicy   a & b    a | b          .at_least(n) / .at_most(n)
```

### The pipeline

A run is a sequence of steps; the **Runner** drives it.

```
  Happy path (one run):

    Benchmark --(1) compile--> ScheduledExecution --(2) execute--> ExecutionResult
        --(3) process + stamp--> Sample --(5) collect--> Report --(6) render--> Formatter / Reporter

  Branches:

    (3') on failure   ExecutionResult --> FailureRecord  (no metrics)  --> Report
    (4)  after a run  Sample(s) --> StoppingPolicy.observe()  (empty list on failure)
                      not converged?  schedule another run  (back to step 1)
```

So: `compile()` is a coroutine that yields one ScheduledExecution per run; the
Runner executes it, the Processor parses successes into Samples (failures become
FailureRecords instead), the StoppingPolicy observes each run's Samples and
decides whether to loop, and everything accumulates into a Report that Formatters
and Reporters render. The step-4 feedback is what makes `.runs(N)`, convergence
policies, and warmup work.

### 1. `Execution` — the pure atom

A description of *one* subprocess invocation. No benchmark identity, no policy,
no I/O. Pure data.

```python
Execution(command: tuple[str, ...], cwd: Path, env, timeout, stdin)
```

`execute(exe) -> ExecutionResult` is the only function that actually spawns a
process. It is pure mechanism — separated from all policy and reporting.

### 2. `ExecutionResult` — the verdict

A tagged union of what happened to one Execution:

```
ExecutionResult = SuccessfulExecutionResult | FailedExecutionResult
```

`returncode` conventions on failure: `124` = timed out, `>0` = non-zero exit,
`-1` = pre-execution failure (command not found / spawn error, with a `reason`).

### 3. `Processor` — `ExecutionResult → Iterable[PartialSample]`

A Processor extracts metrics from process output. The base contract is two hooks:

```python
process(pr)     -> Iterable[PartialSample]   # the metrics
is_success(pr)  -> bool                       # default: exited 0
```

A `PartialSample` is `(metric, value, unit, lower_is_better)` — a measurement
*before* the runner stamps benchmark identity onto it.

**Algebra:**

| Operator / method        | Meaning                                                      |
|--------------------------|--------------------------------------------------------------|
| `a \| b`                 | **Pipeline**: run both, concatenate samples. `is_success` = both succeed. Associative & flattening. |
| `.lower_is_better()`     | Tag emitted samples as lower-is-better (e.g. runtime).       |
| `.higher_is_better()`    | Tag as higher-is-better (e.g. throughput).                   |
| `.when(predicate)`       | Emit only when `predicate(pr)` is true.                      |

The success gate matters: **a failed run emits no metrics.** `process()` is
only called when `is_success(pr)` holds; a failure is instead recorded as a
structured `FailureRecord` (exit code + diagnostic), never as a bogus metric
that would skew rankings. Every run still counts toward the policy, so
`.runs(N)` means "N attempts" — a crashing benchmark stops after `N` runs
rather than retrying.

**Built-ins** (via the `P` namespace):

| Builder                               | Parses                                          |
|---------------------------------------|-------------------------------------------------|
| `P.float_per_line(unit, metric)`      | one float per non-empty stdout line             |
| `.first_line()` / `.last_line()` / `.nth(i)` | …restricted to one line (1-based; negatives from end) |
| `P.regex(metric, pattern, ...)`       | metric values via regex over stdout/stderr      |
| `P.rebench()`                         | ReBench log format                              |
| `P.time(elapsed, user, system)`       | wall/user/system seconds (lower-is-better)      |
| `P.max_rss()`                         | peak RSS in kB (lower-is-better)                |
| `P.rusage(field, metric, unit)`       | any `resource.struct_rusage` field              |
| `P.constant(metric, value, unit)`     | a fixed sample (e.g. a constant run marker)     |

```python
# parse the last stdout line as seconds, also record peak RSS and CPU time
P.float_per_line("s").last_line().lower_is_better() | P.max_rss() | P.time(user=True)
```

### 4. `StoppingPolicy` / `PolicyState` — when to stop

The configuration (`StoppingPolicy`) is **frozen and hashable**; the mutable
per-run state (`PolicyState`) is created by `policy.start()`. This split keeps
benchmarks immutable while letting each *run* of a benchmark carry live state.

```python
policy.start() -> PolicyState
state.observe(run, samples)   # called once per run; samples is [] on failure
state.converged() -> bool
```

**Algebra:**

| Combinator          | Converged when…                       | `max_runs()`        |
|---------------------|---------------------------------------|---------------------|
| `a & b`             | **both** converged                    | `max(a, b)`         |
| `a \| b`            | **either** converged                  | `min(a, b)`         |
| `a.at_least(n)`     | `a & FixedRuns(n)`                    | `max(a, n)`         |
| `a.at_most(n)`      | `a \| FixedRuns(n)`                   | `min(a, n)`         |

Two static introspection methods let consumers reason about a policy without
`isinstance` checks (and the runner uses them to decide parallel fan-out):

- `max_runs() -> int | None` — upper bound on runs (`None` = ∞).
- `independent() -> bool` — whether runs may be reordered / parallelized.

**Built-ins:**

- `FixedRuns(n)` — converge after `n` runs (success or failure). Independent, bounded.
- `CoefficientOfVariation(metric, threshold=0.02, window=5, min_runs=10)` —
  converge once the rolling stdev/mean of `metric` over the last `window` runs
  drops below `threshold`. Order-dependent (sequential), unbounded.
- `Custom(state_factory)` — wrap any `() -> PolicyState`.

```python
# run until stable, but never fewer than 5 or more than 50 runs
CoefficientOfVariation("runtime", threshold=0.03).at_least(5).at_most(50)
# all combinators together
(FixedRuns(20) & CoefficientOfVariation("runtime")) | FixedRuns(100)
```

### 5. `Benchmark` — a generator of Executions

A frozen value object with **one phase function** reused for two phases:

```
warmup:  StoppingPolicy = FixedRuns(0)   # runs reported, NOT fed to measure
measure: StoppingPolicy = FixedRuns(1)   # runs reported AND drive convergence
```

`benchmark.compile(ctx) -> Generator[ScheduledExecution, list[Sample], None]`
is a **coroutine**: it `yield`s the next ScheduledExecution and the runner
`send()`s back the parsed samples, so the stopping policy can observe and decide
whether to continue. Warmup samples are tagged `phase="warmup"` — excluded from
stats by default, but present in JSON/CSV/dir outputs.

Builders (all return a new Benchmark — immutable):

```python
bench("name", path=...)            # **data become benchmark.<attr>
   .with_command([...] | fn)       # fn = (benchmark, ctx) -> argv
   .with_cwd(path | fn)
   .with_env(mapping | fn)
   .with_timeout(seconds)
   .with_process(processor)
   .with_warmup(policy | int)
   .with_measure(policy | int)
   .runs(n)                        # sugar for .with_measure(FixedRuns(n))
```

### 6. `Suite` — a collection with propagating defaults

A frozen `list[Benchmark]` + name. Its `.with_*` methods apply the same builder
to every member **but only where the benchmark's value is still unset**, so
per-benchmark overrides win over suite defaults.

```python
suite("Lox", b1, b2)
   .with_command(fn) / .with_cwd(...) / .with_env(...) / .with_timeout(...)
   .with_process(...) / .with_warmup(...) / .with_measure(...) / .runs(n)
   .add(b) / .add_all(*bs) / .filter(pred) / .named(name)
   .from_files(root, pattern=..., exclude=...)   # one Benchmark per file
   .matrix(axis, values, command=..., env=..., info=...)   # cross product
```

`from_files` and `matrix` can defer to **factories** that run at materialization
time, so file discovery and matrix expansion can depend on `ctx`. `matrix`
stamps the axis value into each Sample's `info` so variants stay distinguishable.

### 7. `Runner` — pumps the coroutines

Consumes `list[Suite]`, flattens to concrete benchmarks (`plan()`), drives each
`compile()` coroutine, and streams results to a Reporter.

- `Sequential` — one benchmark at a time, in order.
- `Parallel(n, fanout=False)` — `n` workers, each driving a full benchmark
  coroutine. With `fanout=True`, benchmarks whose policies are *independent* and
  *bounded* (e.g. `FixedRuns`) have their individual runs spread across workers;
  convergence-driven benchmarks stay sequential.
- `Dry` — advance each coroutine once and print what *would* run; no subprocess.

A defensive `max_runs_per_phase` backstop and `max_consecutive_failures` cap
protect against custom policies that never converge or benchmarks that always
fail.

### 8. `Sample` / `Report` — the immutable record

```python
Sample(suite, benchmark, info, run, phase, metric, value, unit, lower_is_better)
FailureRecord(suite, benchmark, info, run, phase, returncode, reason, message)
Report(samples: list[Sample], metadata: dict, failures: list[FailureRecord])
```

Pure data — no live references to Executions or Processors — so a Report
round-trips through JSON (`report_to_json` / `report_from_json`). `info` is a
canonical sorted tuple of `(key, value)` pairs identifying a matrix variant.
Failed runs never reach `samples`; they land in `failures` as `FailureRecord`s
(exit code + a short diagnostic excerpt), which the summary lists separately.

### 9. `Reporter` vs `Formatter` — two ways out

- **`Reporter`** — a *streaming sink* called live by the runner:
  `start(plan)` once, `sample(sched, pr, samples)` per execution, `finalize()`
  once. Built-ins: `Mixed` (fan-out), `Csv`, `Json`, `Dir` (per-run file tree),
  `Table`, `Progress` (live bar / plain lines off-TTY), `Summary`.
- **`Formatter`** — a *pure* `Report -> str` for human summaries:
  `DefaultSummary` (per-benchmark stats + hyperfine-style ranking + baseline
  comparison) and `Compact` (one line per benchmark, good for commit messages).

The `Summary` reporter buffers samples and delegates to a `Formatter` at the
end, then appends a `Failures:` block from the report's `FailureRecord`s. Stats
live in `report/stats.py` (`group`, `build_summary`, ratios,
`geomean_with_sigma`); they exclude warmup by default and fold `failures` into
each benchmark's run counts.

---

## User parameters (`ctx`)

A script declares its CLI parameters as a `@dataclass`. `run(..., params=Cls)`
auto-generates argparse flags from the field annotations and passes a typed
instance to every builder lambda as `ctx`.

```python
from dataclasses import dataclass
from benchr import Path, run, suite, bench, P

@dataclass
class Params:
    binary: Path            # required  -> --binary PATH
    size: int = 100         # optional  -> --size INT   (default: 100)

def cmd(b, ctx: Params):
    return [str(ctx.binary), str(ctx.size)]

run(suite("s", bench("x")).with_cwd(".").with_command(cmd).with_process(P.time()),
    params=Params)
```

Supported field types: `str`, `int`, `float`, `bool` (→ `--flag/--no-flag`),
`Path`, and `Optional[T]`. **Note:** do not add `from __future__ import
annotations` to a params dataclass — it stringifies annotations and type
coercion falls back to `str` (see *Known issues*).

---

## CLI

```
benchr bench   [--runs N] [--warmup N] [--timeout T] [--jobs J]
               [--json F] [--csv F] [--dir D] [--compare base.json ...]
               [--metric M] CMD1 CMD2 ...

benchr compare a.json b.json ...   [--metric m1,m2]   # first file = baseline
benchr show    out.json            [--metric m]
```

A benchmark script built with `run(...)` accepts the benchr flags plus its own
`@dataclass` flags:

```
python my_bench.py [--<user params>] [--runs N] [--warmup N] [--jobs J]
                   [--quiet] [--dry] [--json F] [--csv F] [--dir D]
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

See `examples/` for one runnable script per capability (matrix, convergence,
JIT warmup, combinators, custom processor/policy, failure handling, baseline
comparison, programmatic use).

---

## Development

```console
uv run pytest          # 124 tests
uv run benchr bench --runs 20 'sleep 0.1' 'sleep 0.2'
```
