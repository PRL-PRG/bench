"""Diagnostics: turn the environment snapshot into actionable warnings.

`run_checks(env)` inspects an `Environment` snapshot and emits warnings, each
carrying the concrete fix command (after Google's "reducing variance" guide).
Every check reads one field and skips itself when that field is `None`, so a
foreign platform silently omits the knobs it cannot observe.

References:
- Reducing Variance - Google Benchmark User Guide (https://google.github.io/benchmark/reducing_variance.html)
"""

from __future__ import annotations

from bench.core.environment import Diagnostic, Environment

# Warn when the 1-minute load exceeds this fraction of the logical CPUs.
LOAD_FRACTION = 0.5


def run_checks(env: Environment) -> list[Diagnostic]:
    """Environment-based warnings; checks for `None` fields are skipped."""
    out: list[Diagnostic] = []

    if env.governors is not None and any(g != "performance" for g in env.governors):
        out.append(
            Diagnostic(
                "high",
                f"CPU frequency scaling enabled (governor: {', '.join(env.governors)}); "
                "real-time measurements will be noisy.",
                "sudo cpupower frequency-set -g performance",
            )
        )
    if env.turbo_enabled:
        out.append(
            Diagnostic(
                "warn",
                "Turbo boost enabled; frequency varies under load.",
                "echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo "
                "(or echo 0 > .../cpufreq/boost)",
            )
        )
    if env.aslr is not None and env.aslr != 0:
        out.append(
            Diagnostic(
                "warn",
                "ASLR enabled; layout-dependent noise is unreproducible.",
                "run under `setarch $(uname -m) -R <cmd>` "
                "or sudo sysctl -w kernel.randomize_va_space=0",
            )
        )
    if env.transparent_hugepage is not None and env.transparent_hugepage != "never":
        out.append(
            Diagnostic(
                "warn",
                f"Transparent huge pages are '{env.transparent_hugepage}'; "
                "background compaction adds latency spikes.",
                "echo never | sudo tee /sys/kernel/mm/transparent_hugepage/enabled",
            )
        )
    if env.smt_enabled:
        out.append(
            Diagnostic(
                "warn",
                "SMT/hyper-threading enabled; sibling threads contend for a core.",
                "echo off | sudo tee /sys/devices/system/cpu/smt/control",
            )
        )
    if env.swap_in_use:
        out.append(
            Diagnostic(
                "warn",
                "Swap is in use; paging adds latency spikes.",
                "sudo swapoff -a (or sudo sysctl -w vm.swappiness=0)",
            )
        )
    if env.on_battery:
        out.append(
            Diagnostic(
                "high",
                "Running on battery; the CPU is likely frequency-capped.",
                "connect AC power",
            )
        )
    if env.low_power_mode:
        out.append(
            Diagnostic(
                "high",
                "Low Power Mode is on; the CPU is throttled.",
                "sudo pmset -a lowpowermode 0",
            )
        )
    if (
        env.load_avg is not None
        and env.logical_cpus
        and env.load_avg[0] > LOAD_FRACTION * env.logical_cpus
    ):
        out.append(
            Diagnostic(
                "warn",
                f"System under load (1-min load {env.load_avg[0]:.1f} "
                f"over {env.logical_cpus} CPUs).",
                "close background processes before benchmarking",
            )
        )
    return out
