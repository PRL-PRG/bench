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
from typing import Literal, cast

from rich.markup import escape as markup_escape

from bench.console.theme import console
from bench.core.fingerprint import Fingerprint
from bench.core.fingerprint.system import (
    LinuxSystemEnvironment,
    MacOSSystemEnvironment,
    SystemEnvironment,
    UnixSystemEnvironment,
    is_system_environment,
)

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
    if not is_system_environment(fp.data):
        return []

    env = fp.data
    out = (
        run_system_checks(cast(SystemEnvironment, env))
        + run_unix_checks(cast(UnixSystemEnvironment, env))
        + run_linux_checks(cast(LinuxSystemEnvironment, env))
        + run_macos_checks(cast(MacOSSystemEnvironment, env))
    )

    return out


def _filter_optional(*ds: Diagnostic | None) -> list[Diagnostic]:
    return [x for x in ds if x is not None]


def _optional_diagnostic(
    flag: bool, severity: Severity, message: str, fix: str | None = None
) -> Diagnostic | None:
    if flag:
        return Diagnostic(severity, message, fix)


def run_system_checks(env: SystemEnvironment) -> list[Diagnostic]:
    load_avg = env.get("load_avg")
    logical_cpus = env.get("logical_cpus")
    return _filter_optional(
        _optional_diagnostic(
            load_avg is not None
            and logical_cpus is not None
            and logical_cpus > 0
            and load_avg[0] > LOAD_FRACTION * logical_cpus,
            "warn",
            f"System under load (1-min load {load_avg[0] if load_avg else 0:.1f} over {logical_cpus} CPUs).",
            "close background processes before benchmarking",
        )
    )


def run_unix_checks(env: UnixSystemEnvironment) -> list[Diagnostic]:
    return _filter_optional(
        _optional_diagnostic(
            env.get("smt_enabled", False),
            "warn",
            "SMT/hyper-threading enabled; sibling threads contend for a core.",
            "echo off | sudo tee /sys/devices/system/cpu/smt/control",
        ),
        _optional_diagnostic(
            env.get("swap_in_use", False),
            "warn",
            "Swap is in use; paging adds latency spikes.",
            "sudo swapoff -a (or sudo sysctl -w vm.swappiness=0)",
        ),
        _optional_diagnostic(
            env.get("on_battery", False),
            "high",
            "Running on battery; the CPU is likely frequency-capped.",
            "connect AC power",
        ),
    )


def run_linux_checks(env: LinuxSystemEnvironment) -> list[Diagnostic]:
    governors = env.get("governors", [])
    thp = env.get("transparent_hugepage", "never")

    return _filter_optional(
        _optional_diagnostic(
            any(g != "performance" for g in governors),
            "high",
            f"CPU frequency scaling enabled (governor: {', '.join(governors)}); real-time measurements will be noisy.",
            "sudo cpupower frequency-set -g performance",
        ),
        _optional_diagnostic(
            env.get("turbo_enabled", False),
            "warn",
            "Turbo boost enabled; frequency varies under load.",
            "echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo (or echo 0 > .../cpufreq/boost)",
        ),
        _optional_diagnostic(
            env.get("aslr", 0) != 0,
            "warn",
            "ASLR enabled; layout-dependent noise is unreproducible.",
            "run under `setarch $(uname -m) -R <cmd>` or sudo sysctl -w kernel.randomize_va_space=0",
        ),
        _optional_diagnostic(
            thp != "never",
            "warn",
            f"Transparent huge pages are '{thp}'; background compaction adds latency spikes.",
            "echo never | sudo tee /sys/kernel/mm/transparent_hugepage/enabled",
        ),
    )


def run_macos_checks(env: MacOSSystemEnvironment) -> list[Diagnostic]:
    return _filter_optional(
        _optional_diagnostic(
            env.get("low_power_mode", False),
            "high",
            "Low Power Mode is on; the CPU is throttled.",
            "sudo pmset -a lowpowermode 0",
        ),
    )


def print_diagnostics(diagnostics: list[Diagnostic], title: str) -> None:
    if not diagnostics:
        return
    console.print(f"\n[bench.label]{title}:[/]")
    for d in diagnostics:
        tag = "[bench.failure]✗[/]" if d.severity == "high" else "[bench.warning]!![/]"
        console.print(f"  {tag} {markup_escape(d.message)}")
        if d.fix:
            console.print(f"      [dim]fix:[/] {markup_escape(d.fix)}")
