"""Rich theme + shared Console used by all reporters."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme


BENCHR_THEME = Theme(
    {
        "bench.success": "green",
        "bench.failure": "red",
        "bench.metric": "cyan",
        "bench.value": "green bold",
        "bench.min": "cyan",
        "bench.max": "magenta",
        "bench.name": "magenta",
        "bench.label": "bold",
        "bench.better": "green bold",
        "bench.worse": "red bold",
        "bench.progress": "blue bold",
        "bench.in_process": "magenta bold",
    }
)

console = Console(theme=BENCHR_THEME, highlight=False)
error_console = Console(stderr=True, theme=BENCHR_THEME, highlight=False)
