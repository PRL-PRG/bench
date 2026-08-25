"""Diagnostics: turn the machine fingerprint into actionable warnings.

`run_checks(fp)` inspects a `Fingerprint` snapshot and emits warnings, each
carrying the concrete fix command (after Google's "reducing variance" guide).
Every check reads one fact and skips itself when the probe did not record it, so
a foreign platform silently omits the knobs it cannot observe.

References:
- Reducing Variance - Google Benchmark User Guide (https://google.github.io/benchmark/reducing_variance.html)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.markup import escape as markup_escape

from bench.console.theme import console
from bench.core.fingerprint import Fingerprint

type Severity = Literal["warn", "high"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One actionable finding. `fix` is a command/setting that resolves it."""

    severity: Severity
    message: str
    fix: str | None = None


# Warn when the 1-minute load exceeds this fraction of the logical CPUs.
LOAD_FRACTION = 0.5


def run_checks(fp: Fingerprint) -> list[Diagnostic]:
    """Fingerprint-based warnings. A check whose fact the probe did not record
    skips itself."""
    out: list[Diagnostic] = []

    governors: list[str] | None = fp.get("governors")
    aslr: int | None = fp.get("aslr")
    thp: str | None = fp.get("transparent_hugepage")
    load_avg: list[float] | None = fp.get("load_avg")
    logical_cpus: int | None = fp.get("logical_cpus")

    if governors is not None and any(g != "performance" for g in governors):
        out.append(
            Diagnostic(
                "high",
                f"CPU frequency scaling enabled (governor: {', '.join(governors)}); "
                "real-time measurements will be noisy.",
                "sudo cpupower frequency-set -g performance",
            )
        )
    if fp.get("turbo_enabled"):
        out.append(
            Diagnostic(
                "warn",
                "Turbo boost enabled; frequency varies under load.",
                "echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo "
                "(or echo 0 > .../cpufreq/boost)",
            )
        )
    if aslr is not None and aslr != 0:
        out.append(
            Diagnostic(
                "warn",
                "ASLR enabled; layout-dependent noise is unreproducible.",
                "run under `setarch $(uname -m) -R <cmd>` "
                "or sudo sysctl -w kernel.randomize_va_space=0",
            )
        )
    if thp is not None and thp != "never":
        out.append(
            Diagnostic(
                "warn",
                f"Transparent huge pages are '{thp}'; "
                "background compaction adds latency spikes.",
                "echo never | sudo tee /sys/kernel/mm/transparent_hugepage/enabled",
            )
        )
    if fp.get("smt_enabled"):
        out.append(
            Diagnostic(
                "warn",
                "SMT/hyper-threading enabled; sibling threads contend for a core.",
                "echo off | sudo tee /sys/devices/system/cpu/smt/control",
            )
        )
    if fp.get("swap_in_use"):
        out.append(
            Diagnostic(
                "warn",
                "Swap is in use; paging adds latency spikes.",
                "sudo swapoff -a (or sudo sysctl -w vm.swappiness=0)",
            )
        )
    if fp.get("on_battery"):
        out.append(
            Diagnostic(
                "high",
                "Running on battery; the CPU is likely frequency-capped.",
                "connect AC power",
            )
        )
    if fp.get("low_power_mode"):
        out.append(
            Diagnostic(
                "high",
                "Low Power Mode is on; the CPU is throttled.",
                "sudo pmset -a lowpowermode 0",
            )
        )
    if (
        load_avg is not None
        and logical_cpus
        and load_avg[0] > LOAD_FRACTION * logical_cpus
    ):
        out.append(
            Diagnostic(
                "warn",
                f"System under load (1-min load {load_avg[0]:.1f} "
                f"over {logical_cpus} CPUs).",
                "close background processes before benchmarking",
            )
        )
    return out


def print_diagnostics(diagnostics: list[Diagnostic], title: str) -> None:
    if not diagnostics:
        return
    console.print(f"\n[bench.label]{title}:[/]")
    for d in diagnostics:
        tag = "[bench.failure]✗[/]" if d.severity == "high" else "[bench.warning]!![/]"
        console.print(f"  {tag} {markup_escape(d.message)}")
        if d.fix:
            console.print(f"      [dim]fix:[/] {markup_escape(d.fix)}")
