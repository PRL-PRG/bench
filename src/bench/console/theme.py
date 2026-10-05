"""Rich theme and the shared stdout/stderr Consoles."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

BENCH_THEME = Theme(
    {
        # Success
        "bench.success": "green",
        "bench.warning": "yellow",
        "bench.failure": "red",
        # Label
        "bench.name": "magenta",
        "bench.label": "bold",
        "bench.metric": "cyan",
        # Values
        "bench.value": "green bold",
        "bench.adjustment": "green",
        "bench.min": "cyan",
        "bench.max": "magenta",
        # Comparison
        "bench.better": "green bold",
        "bench.worse": "red bold",
        # Progress
        "bench.progress": "blue bold",
    }
)

console = Console(theme=BENCH_THEME, highlight=False)
error_console = Console(stderr=True, theme=BENCH_THEME, highlight=False)
