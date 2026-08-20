#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""SPEC CPU 2026 via the `runcpu` harness.

Autodiscovers the four reportable suites (those whose `.bset` metric is
`CINT2026`/`CFP2026`) under `benchspec/CPU/` and runs each benchmark once:
`runcpu --iterations=N` does N iterations in one process and writes a
`Success ... runtime=...` line per iteration to its logfile.

The wrinkle is that runcpu writes its measurements to a logfile rather than to
stdout, and reveals the path only at the end. The old `spec_monitor` polled for
that file and tailed it while the process ran. Nothing needs to stream now, so
this is just a `MetricSource`: a `(InvocationResult) -> str` that runs *after*
the process exits, finds this run's log and hands its text to the metrics. Each
`Regex` carries `iterate=True`, so the Nth `Success` line becomes `Iteration` N.

Still sequential-only: `latest_run_log` picks the newest logfile, which would
race if several runcpu processes wrote at once.
"""

import json
from collections.abc import Callable
from pathlib import Path

from bench import (
    Context,
    InvocationResult,
    Regex,
    SharedBenchParams,
    SuiteBuilder,
    bench,
    bench_app,
    suite,
)

# The four reportable SPEC CPU 2026 suites are exactly those whose .bset metric
# is one of these. Everything else is an aggregate or a build subset.
SUITE_METRICS = {"CINT2026", "CFP2026"}


class Spec2026Params(SharedBenchParams):
    spec_root: Path  # required: the cpu2026 dir (shrc, bin/runcpu)
    config: str = "myconfig.cfg"
    size: str = "test"  # test | train | ref
    tune: str = "base"  # base | peak
    iterations: int = 3


def latest_run_log(result_dir: Path) -> Path | None:
    """The most recently modified `CPU2026.*.log` in `result_dir`, or None."""
    logs = sorted(result_dir.glob("CPU2026.*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def log_source(result_dir: Path) -> Callable[[InvocationResult], str]:
    """A MetricSource reading runcpu's logfile instead of the captured stdout.

    Called once per execution, after the process has exited, so the log is
    complete and there is nothing to poll for.
    """

    def read(_result: InvocationResult) -> str:
        log = latest_run_log(result_dir)
        return log.read_text() if log is not None else ""

    return read


def _command(ctx: Context[Spec2026Params]):
    p = ctx.params
    # exec so the spawned process *is* runcpu, so a SIGINT terminates runcpu
    # directly rather than leaving it orphaned behind bash.
    return [
        "bash",
        "-c",
        "source ./shrc >/dev/null 2>&1; "
        f"exec runcpu --config={p.config} --size={p.size} --tune={p.tune} "
        f"--iterations={p.iterations} {ctx.benchmark}",
    ]


def _success(key: str, capture: str) -> str:
    # Anchor to the per-iteration "Success <bench> ..." line so unrelated log
    # noise (e.g. sysinfo notes) that happens to mention a key is never matched.
    return rf"(?m)^\s*Success\b.*\b{key}=({capture})"


def _metrics(source: Callable[[InvocationResult], str]):
    """One sample per `Success` line, indexed in order: iteration N of every
    metric lands in Iteration N."""
    return (
        Regex(
            "runtime", _success("runtime", r"[\d.]+"), source, unit="s", iterate=True
        ).lower_is_better(),
        Regex(
            "ratio", _success("ratio", r"[\d.]+"), source, iterate=True
        ).higher_is_better(),
        Regex(
            "max_rss", _success("max_rss_kib", r"\d+"), source, unit="kB", iterate=True
        ).lower_is_better(),
        Regex(
            "user_time",
            _success("user_time", r"[\d.]+"),
            source,
            unit="s",
            iterate=True,
        ).lower_is_better(),
        Regex(
            "sys_time", _success("sys_time", r"[\d.]+"), source, unit="s", iterate=True
        ).lower_is_better(),
    )


def discover_suites(p: Spec2026Params) -> list[SuiteBuilder]:
    """Build one bench suite per reportable SPEC suite under `--spec-root`.

    Globs `benchspec/CPU/*.bset`, keeps the manifests whose metric is in
    `SUITE_METRICS`, and wires each suite's members (minus validation-only
    `no_output` entries) as one-process benchmarks sharing the log-reading
    metric source.
    """
    spec_root = p.spec_root
    cpu = spec_root / "benchspec" / "CPU"
    if not cpu.is_dir():
        raise FileNotFoundError(f"no SPEC benchspec dir: {cpu}")
    metrics = _metrics(log_source(spec_root / "result"))

    suites: list[SuiteBuilder] = []
    for bset in sorted(cpu.glob("*.bset")):
        spec = json.loads(bset.read_text())
        if spec.get("metric") not in SUITE_METRICS:
            continue
        skip = set(spec.get("no_output", []))  # e.g. specrand: validation-only
        members = [n for n in spec["benchmarks"] if n not in skip]
        # cwd/command/timeout/metric are suite-wide; each member is just a bare
        # benchmark name. `--iterations` (not `.with_runs`) sets how many
        # iterations one runcpu process does.
        suites.append(
            suite(spec["name"], *(bench(n) for n in members))
            .with_runs(1)
            .with_cwd(lambda ctx: ctx.params.spec_root)
            .with_command(_command)
            .with_timeout(3600)
            .with_metric(*metrics)
        )
    return suites


bench_app(params=Spec2026Params).generator(discover_suites).run()
