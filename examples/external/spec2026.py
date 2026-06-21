#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "../..", editable = true }
# ///
"""SPEC CPU 2026 via the ``runcpu`` harness.

SPEC organises its workloads as *suites* (``intrate``, ``intspeed``,
``fprate``, ``fpspeed``, plus many subset variants) made of *benchmarks*
(``706.stockfish_r``, ``821.gcc_s``, …; ``_r`` = rate, ``_s`` = speed). Each
suite is a JSON file ``benchspec/CPU/<suite>.bset`` listing its members, so the
benchmark list is **autodiscovered** from the chosen suite rather than
hardcoded. ``runcpu`` infers rate-vs-speed from each benchmark's suffix.

runcpu writes per-iteration results to a logfile, not stdout — each iteration
lands as a line like::

     Success 706.stockfish_r base test ratio=0.00, runtime=0.829695, copies=1,
     threads=1, ..., max_rss_kib=144896, sys_time=0.04, user_time=0.65

So the command sources ``shrc`` (sets $SPEC/$PATH), runs ``runcpu`` with
``--iterations N`` (runcpu owns the repetition), then ``cat``s the logfile to
stdout. The exact logfile is taken from runcpu's own ``The log for this run is
in …`` line printed on stdout — no racy "newest file" guessing. A Regex per
metric, anchored to the ``Success`` line, pulls the N per-iteration values out
of it. SPEC writes its results/builds to the default in-tree ``$SPEC/result/``.
Sequential only is assumed (one runcpu at a time).
"""

import json
from dataclasses import dataclass
from pathlib import Path

from benchr import BenchmarkSpec, Context, Regex, bench, run, suite


@dataclass
class Spec2026Params:
    spec_root: Path                            # required: the cpu2026 dir (shrc, bin/runcpu)
    suite: str = "intrate"                     # intrate | intspeed | fprate | fpspeed | ...
    config: str = "myconfig.cfg"
    size: str = "test"                         # test | train | ref
    tune: str = "base"                         # base | peak
    iterations: int = 3


def discover(ctx: Context[Spec2026Params]) -> list[BenchmarkSpec]:
    """Read the suite's .bset (a JSON manifest) for its member benchmarks."""
    bset = ctx.params.spec_root / "benchspec" / "CPU" / f"{ctx.params.suite}.bset"
    spec = json.loads(bset.read_text())
    skip = set(spec.get("no_output", []))  # e.g. specrand: run for validation, not scored
    return [bench(name) for name in spec["benchmarks"] if name not in skip]


def _command(ctx: Context[Spec2026Params]):
    p = ctx.params
    script = (
        "set -e; source ./shrc >/dev/null 2>&1; "
        'o="$(mktemp)"; '
        f"runcpu --config={p.config} --size={p.size} --tune={p.tune} "
        f"--iterations={p.iterations} {ctx.benchmark} "
        '>"$o" 2>&1 || { cat "$o" >&2; rm -f "$o"; exit 1; }; '
        'cat "$o" >&2; '  # surface runcpu's chatter on stderr for debugging
        'log="$(sed -n "s/.*The log for this run is in //p" "$o" | tail -1)"; '
        'rm -f "$o"; cat "$log"'  # the exact logfile -> stdout, parsed below
    )
    return ["bash", "-c", script]


def _success(key: str, capture: str) -> str:
    # Anchor to the per-iteration "Success <bench> ..." line so unrelated log
    # noise (e.g. sysinfo notes) that happens to mention a key is never matched.
    return rf"(?m)^\s*Success\b.*\b{key}=({capture})"


spec2026 = (
    suite("SPEC CPU 2026")
    .factory(discover)
    .with_cwd(lambda ctx: ctx.params.spec_root)
    .with_command(_command)
    .with_timeout(3600)
    .with_metric(
        Regex("runtime", _success("runtime", r"[\d.]+"), unit="s").lower_is_better(),
        Regex("ratio", _success("ratio", r"[\d.]+")).higher_is_better(),
        Regex("max_rss", _success("max_rss_kib", r"\d+"), unit="kB").lower_is_better(),
        Regex("user_time", _success("user_time", r"[\d.]+"), unit="s").lower_is_better(),
        Regex("sys_time", _success("sys_time", r"[\d.]+"), unit="s").lower_is_better(),
    )
)


if __name__ == "__main__":
    run(spec2026, params=Spec2026Params)
