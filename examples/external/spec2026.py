#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""SPEC CPU 2026 via the `runcpu` harness - harness mode.

SPEC organises its workloads as *suites* (`intrate`, `intspeed`, `fprate`,
`fpspeed`) made of *benchmarks* (`706.stockfish_r`, `821.gcc_s`, ..., where `_r` = rate,
`_s` = speed). Each suite is a JSON manifest `benchspec/CPU/<suite>.bset`
listing its members.

Both layers are **autodiscovered**: `discover_suites` globs `benchspec/CPU/`
and builds one bench suite per reportable SPEC suite (those whose `.bset`
metric is `CINT2026` or `CFP2026`, the four canonical suites. The ~78 other
`.bset` files are aggregates like `CPU`/`specrate` or build subsets like
`fprate_pure_c`, and are skipped). Each suite's member benchmarks come straight
from its `.bset`. There is no `--suite` flag, so running the script benchmarks
every suite.

`runcpu` appends one line to a *logfile* (not stdout) as each iteration
finishes:

```text
Success 706.stockfish_r base test ratio=0.00, runtime=0.829695, copies=1, ...,
max_rss_kib=144896, sys_time=0.04, user_time=0.65
```

That makes it a textbook bench *harness*: one long-running process streaming
many iterations. `spec_monitor` tails the logfile and frames each `Success`
line into one observation, and the per-metric `Regex` (anchored to the `Success`
line) turns it into samples. Because the log grows incrementally, bench sees
iterations live and the stopping policy can end the run early.

Finding the logfile is the one wrinkle: `runcpu` prints its path only at the
very end of the run, so the monitor cannot read it from stdout up front.
Instead `latest_run_log` locates *this run's* log, the `CPU2026.*.log` that
appears after launch, and the monitor tails it.

Stopping policy: each benchmark uses `FixedRuns(iterations)`, so bench
collects exactly `--iterations` samples and then kills `runcpu` (skipping its
lengthy report generation. The OS releases SPEC's `flock` on exit, so the next
benchmark is unaffected). To stop early once measurements stabilise (worth it
for long `train`/`ref` runs) raise `--iterations` and swap the policy for
`CoefficientOfVariation(...)`.

Sequential only is assumed (one `runcpu` at a time, since the newest-logfile lookup
would race otherwise). SPEC writes its results to the default in-tree
`$SPEC/result/`.
"""

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from bench import (
    Context,
    FixedRuns,
    HarnessHandle,
    HarnessMonitor,
    Regex,
    Suite,
    bench,
    run,
    suite,
)

# The four reportable SPEC CPU 2026 suites are exactly those whose .bset metric
# is one of these. Everything else is an aggregate or a build subset.
SUITE_METRICS = {"CINT2026", "CFP2026"}


@dataclass
class Spec2026Params:
    spec_root: Path  # required: the cpu2026 dir (shrc, bin/runcpu)
    config: str = "myconfig.cfg"
    size: str = "test"  # test | train | ref
    tune: str = "base"  # base | peak
    iterations: int = 3


def latest_run_log(result_dir: Path) -> Path | None:
    """The most recently modified `CPU2026.*.log` in `result_dir`.

    Args:
        result_dir: SPEC's `result/` directory, where `runcpu` writes its logs.

    Returns:
        The newest logfile by mtime, or `None` if none exist yet.
    """
    logs = sorted(result_dir.glob("CPU2026.*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def make_monitor(result_dir: Path) -> HarnessMonitor:
    """Build a harness monitor that frames `runcpu`'s logfile into iterations.

    `runcpu` reveals its logfile path only at the end of the run, so the monitor
    waits for *this run's* log to appear (the `CPU2026.*.log` that shows up after
    launch, distinct from any pre-existing one), then tails it, yielding each
    per-iteration `Success ... runtime=...` line as one observation block.

    Args:
        result_dir: SPEC's `result/` directory to watch.

    Returns:
        A `HarnessMonitor` closure over `result_dir`.
    """

    def is_iteration(line: str) -> bool:
        return line.startswith("Success") and "runtime=" in line

    def spec_monitor(handle: HarnessHandle) -> Iterator[str]:
        # Wait for the logfile created by this run (runcpu makes it a beat after
        # spawn). Ignore any log left over from a previous run.
        baseline = latest_run_log(result_dir)
        log = baseline
        while handle.is_alive() and log == baseline:
            time.sleep(0.1)
            log = latest_run_log(result_dir)
        if log is None or log == baseline:
            return  # process died before producing a logfile

        with open(log) as f:
            while True:
                pos = f.tell()
                line = f.readline()
                if line.endswith("\n"):
                    s = line.strip()
                    if is_iteration(s):
                        yield s
                elif handle.is_alive():
                    f.seek(pos)  # partial line mid-write, re-read when complete
                    time.sleep(0.05)
                else:
                    return  # process gone and nothing more to read

    return spec_monitor


def _command(ctx: Context[Spec2026Params]):
    p = ctx.params
    # exec so the spawned process *is* runcpu, so bench's convergence-kill then
    # terminates runcpu directly rather than leaving it orphaned behind bash.
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


_METRICS = (
    Regex("runtime", _success("runtime", r"[\d.]+"), unit="s").lower_is_better(),
    Regex("ratio", _success("ratio", r"[\d.]+")).higher_is_better(),
    Regex("max_rss", _success("max_rss_kib", r"\d+"), unit="kB").lower_is_better(),
    Regex("user_time", _success("user_time", r"[\d.]+"), unit="s").lower_is_better(),
    Regex("sys_time", _success("sys_time", r"[\d.]+"), unit="s").lower_is_better(),
)


def discover_suites(p: Spec2026Params) -> list[Suite]:
    """Build one bench suite per reportable SPEC suite found under `--spec-root`.

    Globs `benchspec/CPU/*.bset` and keeps the manifests whose metric is in
    `SUITE_METRICS` (the four canonical suites). Each suite's benchmarks are read
    from its `.bset` (minus the validation-only `no_output` entries) and wired as
    harness benchmarks sharing the log-tailing monitor.

    Passed straight to `run` as the suite factory. bench calls it after parsing
    the CLI, so `p.spec_root` (where the `.bset` files live) is already resolved.

    Args:
        p: the parsed params. `p.spec_root` is the cpu2026 install dir
            (contains `shrc`, `benchspec/`).

    Returns:
        One `Suite` per discovered SPEC suite, ordered by `.bset` filename.
    """
    spec_root = p.spec_root
    cpu = spec_root / "benchspec" / "CPU"
    if not cpu.is_dir():
        raise FileNotFoundError(f"no SPEC benchspec dir: {cpu}")
    monitor = make_monitor(spec_root / "result")

    def runs(ctx: Context[Spec2026Params]) -> FixedRuns:
        return FixedRuns(ctx.params.iterations)

    suites: list[Suite] = []
    for bset in sorted(cpu.glob("*.bset")):
        spec = json.loads(bset.read_text())
        if spec.get("metric") not in SUITE_METRICS:
            continue
        skip = set(spec.get("no_output", []))  # e.g. specrand: validation-only
        members = [n for n in spec["benchmarks"] if n not in skip]
        # harness/monitor and the per-iteration runs policy are suite-wide, so
        # they live on the suite alongside cwd/command/timeout/metric. Each
        # member is just a bare benchmark name.
        suites.append(
            suite(spec["name"], *(bench(n) for n in members))
            .with_harness(monitor)
            .with_runs(runs)
            .with_cwd(lambda ctx: ctx.params.spec_root)
            .with_command(_command)
            .with_timeout(3600)
            .with_metric(*_METRICS)
        )
    return suites


run(discover_suites, params=Spec2026Params)
