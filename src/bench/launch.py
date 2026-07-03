"""Launch helpers: build argv prefixes you prepend to a benchmark command.

Use:

    bench("b").with_command([*taskset(0), *nice(-20), "./workload"])

Because the prefix lives in the command, it is recorded verbatim in the report.
"""

from __future__ import annotations

from collections.abc import Iterable


def _cpu_spec(cpus: int | str | Iterable[int]) -> str:
    if isinstance(cpus, int):
        return str(cpus)
    if isinstance(cpus, str):
        return cpus
    return ",".join(str(c) for c in cpus)


def taskset(cpus: int | str | Iterable[int]) -> list[str]:
    """`taskset -c <spec>` pinning to the given CPU(s) (Linux)."""
    return ["taskset", "-c", _cpu_spec(cpus)]


def nice(n: int) -> list[str]:
    """`nice -n <n>` (negative values raise priority, which needs privilege)."""
    return ["nice", "-n", str(n)]


def setarch_no_aslr() -> list[str]:
    """`setarch -R` disabling ASLR for the child only (Linux)."""
    return ["setarch", "-R"]
