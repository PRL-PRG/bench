# bench

A benchmarking framework and command-line tool.

Two ways to use it:

* **`bench run`** — ad-hoc command benchmarking from the shell.
* **`run(suite, …)`** — declarative Python scripts for repeatable configurations.

## Quick start

### As a CLI

```console
$ cat fib.py
def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)

fib(35)

$ bench run --runs 5 --time 0 'python3.9 fib.py' 'python3.14 fib.py'
run/python3.9 fib.py: 0|5 runs
  elapsed [s] (mean ± σ):  1.39 ± 0.01    (1.38 … 1.40)

run/python3.14 fib.py: 0|5 runs
  elapsed [ms] (mean ± σ):  940.28 ± 2.53    (936.87 … 943.59)

Summary
  'python3.14 fib.py' [elapsed] was
    1.47 ± 0.01 times lower than 'python3.9 fib.py'
```

`0|5 runs` means **0 failures | 5 successes** — Python 3.14 runs the recursive
`fib(35)` ~1.5× faster than 3.9. (`--time 0` lifts the default 3-second
per-command budget so all five runs happen.)

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

Every CLI flag (`--jobs`, `--json`, `--csv`, `--dir`, `--compare`, `--dry`,
`--verbose`, …) also works on a `run(...)` script, and CLI flags override what
the script set in code. See [`examples/`](examples/) for one runnable script
per capability.

## How it works

A run is a pipeline: you build **suites** of **benchmarks**, the framework
resolves them into concrete variants, executes each one collecting
measurements, and produces a **report**.

The building blocks:

* **Suite** — a named collection of benchmarks plus the defaults they inherit.
* **Benchmark** — command + cwd + env + metrics + stopping policy + variants.
* **Run** — one process execution: identity + outcome + its iterations (plus any whole-process samples).
* **Iteration** — one measured iteration: a bag of samples (or a failure).
* **Sample** — one metric value (name, value, unit).
* **Report** — the list of runs, JSON round-trippable.

A failed run still produces a `Run` (with `failure` set and no samples), so a
crashing benchmark shows up as `3|0 runs` with the reason listed instead of
poisoning the stats with fake numbers.

The pieces you plug in:

* **Metric** — turns a run into samples. Two kinds: an `IterationMetric`
  parses each iteration's text (`Regex`, `FloatPerLine`, `Rebench`), a
  `ProcessMetric` reads the finished process (`Time`, `RUsage`, `max_rss()`,
  and `PerfStat` for `perf stat` hardware counters).
* **StoppingPolicy** — decides when enough runs have been collected:
  `FixedRuns(n)`, `MaxDuration(seconds)`, `CoefficientOfVariation(...)`,
  combined with `&` / `|` / `.at_least(n)` / `.at_most(n)`.
* **Runner** — `Sequential` (default), `Parallel(workers)` (per-benchmark
  concurrency, only sound when wall-clock time isn't the metric), or `Dry`
  (print what would run, no subprocess).
* **OutlierDetection** — flags anomalous samples (kept in the report, not
  dropped): `ModifiedZScore`, or `NoDetection` to switch it off.

**Matrix** benchmarks (`.with_matrix(**dims)`) expand into the cross-product of
dimension values, each cell a variant. **Harness** benchmarks
(`.with_harness()`) run the command once and let the process iterate
internally (e.g. for JIT warmup), each reported iteration becoming an
`Iteration` of that single run.

**Outputs**: live progress to the terminal, plus optional `--json`, `--csv`,
`--dir` (per-execution tree), and `--compare baseline.json` to diff against a
saved report. `bench compare a.json b.json …` does the same diff after the fact
from saved reports.

**Environment & noise**: `bench doctor` snapshots the machine and flags noise
sources (CPU governor, turbo, ASLR, …), exiting non-zero on a high-severity
issue so it can gate a session. On Linux as root, `bench denoise
minimize|restore|status` quiets those knobs and reverts them. Per run,
`--check-environment` records the snapshot and runs the checks (off by default)
and `--denoise` minimizes the knobs for the run and restores them after.

## Development

```console
uv run pytest
uv run bench run --runs 20 'sleep 0.1' 'sleep 0.2'
```
