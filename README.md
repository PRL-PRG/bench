# bench

A lightweight Python benchmarking framework and command-line tool.

Two ways to use it:

* **`bench run`** - ad-hoc command benchmarking from the shell.
* **`run(suite, ...)`** - declarative Python scripts for repeatable configurations.

## Quick start

### As a CLI

```console
$ cat fib.py
def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)

fib(35)

$ bench run --runs 5 'python3.9 fib.py' 'python3.14 fib.py'
run   elapsed [ms]
  matrix              mean ± σ        min … max
  python3.9 fib.py    1392.4 ± 9.8    (1381.0 … 1403.2) (5 runs)
  python3.14 fib.py   947.1 ± 5.6     (940.2 … 954.9) (5 runs)

Summary - run (elapsed)
  python3.14 fib.py was
  1.47 ± 0.01× better than python3.9 fib.py (5 runs)
```

`(5 runs)` is the count of successful runs - Python 3.14 runs the recursive
`fib(35)` ~1.5x faster than 3.9.

### As a script

```python
from bench import Time, bench, run, suite

s = (
    suite("fib",
        bench("py3.9").with_command(["python3.9", "fib.py"]),
        bench("py3.14").with_command(["python3.14", "fib.py"]),
    )
    .with_process_metric(Time())   # measure wall-clock elapsed
    .with_runs(5)                  # 5 measured runs each
)

if __name__ == "__main__":
    run(s)
```

```console
python compare.py --json out.json
```

Every CLI flag (`--jobs`, `--json`, `--csv`, `--dir`, `--dry`, `--verbose`,
`--include`/`--exclude`, `--list`, `--show`, ...) also works on a `run(...)`
script, and CLI flags override what the script set in code. See
[`examples/`](examples/) for one runnable script per capability.

## How it works

Running `bench` is a pipeline: you build **suites** of **benchmarks**, the
framework resolves them into concrete variants, executes each one collecting
measurements, and produces a **report**.

The building blocks, from the outside in:

* **Suite** - a named collection of benchmarks plus the defaults they inherit.
* **Benchmark** - command + cwd + env + metrics + stopping policy + variants;
  resolves to one **Invocation** per variant.
* **Invocation** - the pure spec of how to start one subprocess (command, cwd, env, timeout, stdin).
* **Execution** - one subprocess run of an Invocation, start to finish: identity
  + outcome + its **Iterations** (plus any whole-process **Samples**).
* **Iteration** - one measurement: a bag of **Samples** (or a failure).
* **Sample** - one metric value (name, value, unit).
* **Report** - the list of Executions, JSON round-trippable.

A command benchmark produces many Executions of one Iteration each; a harness
produces one Execution of many Iterations.

A failed run still produces an `Execution` (with `failure` set and no samples),
so a crashing benchmark is listed under a `Failures:` block with its reason - and
a variant that only sometimes fails shows a `(failures|runs)` count next to its
stats - instead of poisoning the stats with fake numbers.

The pieces you plug in:

* **Metric** - turns a run into samples. Two kinds: an `IterationMetric`
  parses each iteration's text (`Regex`, `FloatPerLine`, `Rebench`), a
  `ProcessMetric` reads the finished process (`Time`, `RUsage`, `max_rss()`,
  and `PerfStat` for `perf stat` hardware counters).
* **StoppingPolicy** - decides when enough runs have been collected:
  `FixedRuns(n)`, `MaxDuration(seconds)`, `CoefficientOfVariation(...)`,
  combined with `&` / `|` / `.at_least(n)` / `.at_most(n)`.
* **Runner** - `Sequential` (default), `Parallel(workers)` (per-benchmark
  concurrency, only sound when wall-clock time isn't the metric), or `Dry`
  (print what would run, no subprocess).
* **OutlierDetection** - flags anomalous samples (kept in the report, not
  dropped): `ModifiedZScore`, or `NoDetection` to switch it off.

**Matrix** benchmarks (`.with_matrix(**dims)`) expand into the cross-product of
dimension values, each cell a variant. **Harness** benchmarks
(`.with_harness()`) run the command once and let the process iterate
internally (e.g. for JIT warmup), each reported iteration becoming an
`Iteration` of that single Execution.

**Outputs**: live progress to the terminal, plus optional `--json`, `--csv`, and
`--dir` (per-execution tree). `bench compare a.json b.json ...` diffs saved
reports after the fact (the first file is the baseline), and `bench show
report.json` re-renders a single saved report.

**Environment & noise**: `bench doctor` snapshots the machine and flags noise
sources (CPU governor, turbo, ASLR, ...), exiting non-zero on a high-severity
issue so it can gate a session. On Linux as root, `bench denoise
minimize|restore|status` quiets those knobs and reverts them. Per run,
`--check-environment` records the snapshot and runs the checks (off by default)
and `--denoise` minimizes the knobs for the run and restores them after.

## Development

```console
uv run pytest
uv run bench run --runs 20 'sleep 0.1' 'sleep 0.2'
```

## Acknowledgements

Much of the bench has been inspired by these great tools:

* [hyperfine](https://github.com/sharkdp/hyperfine) - CLI ergonomics and comparison output
* [ReBench](https://github.com/smarr/ReBench) - configuration-driven design and the built-in `Rebench` metric

## License

[MIT](LICENSE)
