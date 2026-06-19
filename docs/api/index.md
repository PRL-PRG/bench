# API reference

Everything below is generated from the source docstrings. Type annotations in
signatures are clickable — follow them to jump between types.

Layering rule: `core ← grammar ← report ← runner ← cli` — every import points
left.

- **[Grammar](grammar.md)** — the builder surface you write:
  [`Suite`][benchr.grammar.suite.Suite], [`Benchmark`][benchr.grammar.benchmark.Benchmark],
  [`Context`][benchr.grammar.context.Context].
- **[Metrics](metrics.md)** — turn run output into samples.
- **[Stopping policies](policies.md)** — decide when enough runs have been taken.
- **[Data model](data.md)** — the pure atoms a run produces
  ([`Execution`][benchr.core.execution.Execution], [`Sample`][benchr.core.sample.Sample],
  [`Report`][benchr.core.sample.Report]).
- **[Runners](runners.md)** — consume planned benchmarks and emit samples.
- **[Reporting](reporting.md)** — streaming sinks, formatters, and statistics.
- **[CLI](cli.md)** — the [`run`][benchr.cli.run] entry point.
