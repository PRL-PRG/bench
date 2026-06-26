# bench

A lightweight Python benchmarking framework.

Two ways to use it:

- **`bench run`** - hyperfine-style CLI for ad-hoc command timing.
- **`run(suite, …)`** - declarative Python scripts for repeatable benchmark
  configurations (file-discovered benchmarks, matrices, custom metrics,
  convergence policies).

## Quick start

### As a CLI

```console
$ bench run --runs 5 --warmup 1 'sleep 0.05' 'sleep 0.1'

bench/sleep 0.05: 0|5 runs
  elapsed [ms] (mean ± σ):  55.22 ± 2.11    (51.83 … 57.35)

bench/sleep 0.1: 0|5 runs
  elapsed [ms] (mean ± σ):  106.79 ± 2.45    (103.94 … 109.83)

Summary
  'sleep 0.05' [elapsed] was
    1.92 ± 0.08 times lower than 'sleep 0.1'
```

`0|5 runs` means **0 failures | 5 successes**.

### As a script

```python
from bench import Time, bench, run, suite

s = (
    suite("demo",
        bench("fast").with_command(["sleep", "0.05"]),
        bench("slow").with_command(["sleep", "0.1"]),
    )
    .with_metric(Time())   # measure wall-clock elapsed
    .with_runs(5)          # 5 measured runs each
)

if __name__ == "__main__":
    run(s)
```

Every CLI flag (`--runs`, `--warmup`, `--jobs`, `--json`, `--csv`, `--dir`,
`--compare`, `--dry`, `--verbose`) also works on a script built with
[`run`][bench.run.run].

## Where next

- **[Tutorial](tutorial.md)** - a guided walkthrough from a one-liner to
  matrices, custom metrics, and convergence policies.
- **[API reference](api/index.md)** - every public type, cross-linked.
