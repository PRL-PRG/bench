# API reference

Everything below is generated from the source docstrings. Type annotations in
signatures are clickable. Follow them to jump between types.

Layering rule: `core ← grammar ← report ← runner ← cli`, every import points
left.

- **[Grammar](grammar.md)** - the builder surface you write:
  [`Suite`][bench.grammar.suite.Suite], [`Benchmark`][bench.grammar.benchmark.Benchmark],
  [`Context`][bench.grammar.context.Context].
- **[Metrics](metrics.md)** - turn run output into samples.
- **[Stopping policies](policies.md)** - decide when enough runs have been taken.
- **[Data model](data.md)** - the pure atoms a run produces
  ([`Execution`][bench.core.execution.Execution], [`Sample`][bench.core.sample.Sample],
  [`Report`][bench.core.sample.Report]).
- **[Runners](runners.md)** - consume planned benchmarks and emit samples.
- **[Reporting](reporting.md)** - streaming sinks, formatters, and statistics.
- **[CLI](cli.md)** - the [`run`][bench.cli.run] entry point.
