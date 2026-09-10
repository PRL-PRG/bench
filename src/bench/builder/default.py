from __future__ import annotations

import re
from pathlib import Path

from bench.model.benchmark import (
    Benchmark,
    BenchmarkPred,
    format_benchmark,
    format_variant,
)
from bench.model.invocation import TIMEOUT_RC, InvocationResult, Verdict
from bench.params import (
    Params,
    SharedReporterParams,
    SharedRunnerParams,
    SharedSelectionParams,
)
from bench.report import (
    CompositeReporter,
    CsvReporter,
    DirReporter,
    JsonReporter,
    ProgressReporter,
    Reporter,
)
from bench.runner import (
    DryRunner,
    ParallelRunner,
    Runner,
    SequentialRunner,
)


def default_label(b: Benchmark) -> str:
    """Default variant label: the formatted `(k=v, ...)` tuple, no parens."""
    return format_variant(b.variant).strip(" ()")


def default_success(result: InvocationResult) -> Verdict:
    """Default success policy: clean exit passes, anything else fails."""
    if result.failure is not None:  # spawn failure already judged by execute()
        return result.failure
    if result.returncode == TIMEOUT_RC:
        return "timeout"
    if result.returncode != 0:
        return f"exit code {result.returncode}"
    return None


def default_reporter(
    params: Params,
    *,
    json: str | Path | JsonReporter | None = None,
    csv: str | Path | CsvReporter | None = None,
    dir: str | Path | DirReporter | None = None,
) -> Reporter | None:
    """Assemble the builtin reporter bundle: a progress bar and the json, csv and
    dir output sinks.

    Each of `json`/`csv`/`dir` is the value to use when the matching CLI flag is
    unset: the flag wins, else this default, else the sink stays off. An app that
    always wants a sink supplies its default here, e.g.
    `with_reporter(lambda p: default_reporter(p, dir=...))`.
    """
    is_params = isinstance(params, SharedReporterParams)

    sinks: list[Reporter] = []

    if is_params and params.progress:
        sinks.append(ProgressReporter())

    # A reporter instance is authoritative: the app configured that sink itself
    # (e.g. a JsonReporter with include_output=True), so it already folded in
    # the flag. Otherwise the flag wins over a path default.
    if isinstance(json, JsonReporter):
        sinks.append(json)
    elif j := ((is_params and params.json) or json):
        sinks.append(JsonReporter(Path(j)))

    if isinstance(csv, CsvReporter):
        sinks.append(csv)
    elif c := ((is_params and params.csv) or csv):
        sinks.append(CsvReporter(Path(c)))

    if isinstance(dir, DirReporter):
        sinks.append(dir)
    elif d := ((is_params and params.dir) or dir):
        sinks.append(DirReporter(Path(d)))

    if len(sinks) == 0:
        return None
    elif len(sinks) == 1:
        return sinks[0]
    else:
        return CompositeReporter(*sinks)


def default_runner(params: Params) -> Runner | None:
    if not isinstance(params, SharedRunnerParams):
        return None

    if params.dry:
        return DryRunner(verbose=params.verbose)
    if params.jobs > 1:
        return ParallelRunner(workers=params.jobs, verbose=params.verbose)
    return SequentialRunner(verbose=params.verbose)


def default_filter(params: Params) -> BenchmarkPred:
    if not isinstance(params, SharedSelectionParams):
        return lambda _: True

    inc = [re.compile(pat) for pat in (params.include or [])]
    exc = [re.compile(pat) for pat in (params.exclude or [])]

    def keep(b: Benchmark) -> bool:
        # Match both spellings of the variant, the canonical `(k=v, ...)` key and
        # the label the reports show, so a pattern written against what the
        # terminal prints selects what the user expects.
        keys = [format_benchmark(b.suite, b.name, b.variant)]
        if b.variant_label:
            keys.append(format_benchmark(b.suite, b.name, b.variant, b.variant_label))
        if inc and not any(r.search(k) for k in keys for r in inc):
            return False
        return not any(r.search(k) for k in keys for r in exc)

    return keep
