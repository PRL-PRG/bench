# bench

[![CI](https://github.com/PRL-PRG/bench/actions/workflows/ci.yml/badge.svg)](https://github.com/PRL-PRG/bench/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Python benchmarking framework and command-line tool: `bench run`
for ad-hoc shell commands, and Python API for repeatable suites.

## Quick start

### As a CLI

```console
$ bench run --runs 5 'sleep 0.01' 'sleep 0.02'
run (elapsed)
  matrix     mean  ± σ       min    … max
  sleep 0.01 11.03 ± 0.36 ms (10.66 … 11.49) (5 runs)
  sleep 0.02 20.76 ± 0.16 ms (20.58 … 20.99) (5 runs)

Comparison - run (elapsed)
  sleep 0.01 was
  1.91 ± 0.07× better than sleep 0.02 (5 runs)
```

### As a script

```python
# compare.py
from bench import Time, bench, run, suite

s = suite(
    "sleep",
    bench("sleep")
    .with_matrix(seconds=["0.01", "0.02"])
    .with_command(lambda ctx: ["sleep", ctx.data.seconds])
    .with_metric(Time())
    .with_runs(5),
)

if __name__ == "__main__":
    run(s)
```

```console
$ python compare.py --json out.json
$ python compare.py show out.json
```

See [`examples/`](examples/) for one runnable script per capability.

## How it works

A **suite** holds **benchmarks** or other **sub-suites**.
Settings set on an app, suite or benchmark are inherited downwards.
Each benchmark expands into one variant per point of
its matrix (`.with_matrix(**dims)`), and each variant is run until its stopping
policy is satisfied (typically fixed number of runs). 
Every run is an **Execution** in a **Report**.
Benchmarks run with an empty environment unless `.with_env(...)` or
`.with_inherit_env()` is used.

Variants can be dropped with `.add_matrix_skip(...)` or `.with_filter(...)`,
runs spaced with `.with_cooldown(s)`, and a suite's order randomized with
`.with_shuffle(seed)`.

| Component         | Setter                         |                                                   |
|-------------------|--------------------------------|---------------------------------------------------|
| Metric            | `.with_metric(...)`            | Turns a finished run into samples.                |
| Stopping policy   | `.with_runs(...)`,             | Decides when a variant has run enough times.      |
|                   | `.with_warmup(...)`            |                                                   |
| Runner            | `.with_runner(...)`            | Schedules variants: sequential, parallel or dry.  |
| Controller        | `.with_controller(...)`        | Runs one variant's executions, e.g. under `perf`. |
| Summary           | `bench_app(summary=...)`       | Renders the statistics after the run.             |
| Hooks             | `.with_hook(...)`              | Runs setup/teardown code around each execution.   |
| Outlier detection | `.with_outlier_detection(...)` | Flags outlying samples; they stay in the stats.   |

**Outputs**: live progress, the summary, and optionally `--json`, `--csv` and
`--dir` (`<dir>/<suite>/<benchmark>[/<variant>]/<run>/`). `bench show
report.json` re-renders a saved report; `bench compare a.json b.json ...`
compares saved reports, the first being the baseline.

**Noise**: `bench doctor` prints the machine fingerprint and flags noise
sources, exiting non-zero on a high-severity one. On Linux as root, `bench
denoise minimize|restore|status` quiets those knobs and reverts them.
`bench run --check-environment` / `--denoise` do the same around a run;
scripts use `bench_app(probe=SystemProbe(), denoise=True)`.

## Development

The linter, typechecker, formatter and tests all run uppon running
```console
make check
```

To format the project use
```console
make format
```

To fix auto-fixable Ruff errors use
```console
make fix-lint
```

## Acknowledgements

Much of the bench has been inspired by these great tools:

* [hyperfine](https://github.com/sharkdp/hyperfine) - CLI ergonomics and comparison output
* [ReBench](https://github.com/smarr/ReBench) - configuration-driven design and the built-in `RebenchMetric`

## License

[MIT](LICENSE)
