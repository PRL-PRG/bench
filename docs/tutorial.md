# Tutorial

This walkthrough goes from a one-liner on the command line to a repeatable
benchmark script with custom metrics and a convergence policy. Every type
named in `code font` links to its [API reference](api/index.md).

## Install

```console
uv add bench
```

## 1. Time a command from the shell

The quickest way in is the `bench run` subcommand. Point it at one or more
shell commands and it times them, hyperfine-style:

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

`--warmup 1` runs each command once before measuring (warmup runs are reported
but dropped from the statistics). `--runs 5` takes five measured runs.

## 2. Turn it into a script

A script gives you something repeatable and version-controllable. Build a
[`suite`][bench.grammar.suite.suite] of [`bench`][bench.grammar.benchmark.bench]
entries and hand it to [`run`][bench.cli.run]:

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

```console
python demo.py --runs 10 --json out.json
```

CLI flags **always override** what the script set in code, so the same file
works for a quick local check and a heavier CI run.

## 3. Measure something other than wall-clock time

A [`Metric`][bench.core.metric.Metric] turns the output of a run into one or
more samples. Wall-clock time ([`Time`][bench.core.metric.Time]) is the
default. The built-ins also parse program output:

```python
from bench import FloatPerLine, Time, max_rss

suite("metrics", bench("x").with_command(["./mybench"]))
    .with_metric(
        FloatPerLine("s").last_line().lower_is_better(),  # last stdout line, in seconds
        max_rss(),                                        # peak RSS from rusage
        Time(user=True),                                  # wall + user CPU
    )
```

- [`FloatPerLine`][bench.core.metric.FloatPerLine] parses numeric stdout lines.
- [`Regex`][bench.core.metric.Regex] pulls values out with a pattern.
- [`Rebench`][bench.core.metric.Rebench] reads the ReBench log format.
- [`max_rss`][bench.core.metric.max_rss] / [`RUsage`][bench.core.metric.RUsage]
  read `rusage` fields.

## 4. Stop when the numbers settle

Instead of a fixed run count, a [`StoppingPolicy`][bench.core.policy.StoppingPolicy]
can run until the measurement converges. [`CoefficientOfVariation`][bench.core.policy.CoefficientOfVariation]
stops once the relative spread of a metric drops below a threshold, and the
[`at_least`][bench.core.policy.StoppingPolicy.at_least] /
[`at_most`][bench.core.policy.StoppingPolicy.at_most] combinators bound it:

```python
from bench import CoefficientOfVariation

# run until CoV is stable, but never fewer than 5 or more than 30 runs
policy = CoefficientOfVariation("elapsed", threshold=0.02).at_least(5).at_most(30)

suite("converge", bench("x").with_command(["./mybench"])).with_runs(policy)
```

## 5. Sweep a matrix of variants

`.with_matrix(**dims)` declares dimensions. Their cartesian product produces the
*variants* of a benchmark. Command/cwd/env callables read the current cell from
[`ctx.matrix`][bench.grammar.context.Context]:

```python
from bench import Regex, bench, suite

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

## 6. Read the results programmatically

A run produces a [`Report`][bench.core.sample.Report], a list of
[`Run`][bench.core.sample.Run] records, each carrying its
[`Observation`][bench.core.sample.Observation]s of
[`Sample`][bench.core.sample.Sample]s. It round-trips through JSON via
[`report_to_json`][bench.core.sample.report_to_json] /
[`report_from_json`][bench.core.sample.report_from_json]:

```python
from bench import Sequential, Time, bench, plan, suite

s = (suite("prog", bench("a").with_command(["sleep", "0.02"]))
     .with_metric(Time()).with_runs(3))

report = Sequential().run(plan([s], None), None)
for run in report.runs:
    for obs in run.observations:
        for sample in obs.samples:
            print(run.benchmark, run.run, sample.metric, sample.value)
print("failures:", report.failures)
```

## Where next

- The [API reference](api/index.md) documents every public type, with
  signatures cross-linked.
- The [`examples/`](https://github.com/fikovnik/bench/tree/master/examples)
  directory has one runnable script per capability (matrices, file discovery,
  harness benchmarks, custom metrics and policies, baseline comparison).
