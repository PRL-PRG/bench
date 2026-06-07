"""Rich theme + shared Console used by all reporters."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme


BENCHR_THEME = Theme(
    {
        "benchr.success": "green",
        "benchr.failure": "red",
        "benchr.metric": "cyan",
        "benchr.value": "green bold",
        "benchr.min": "cyan",
        "benchr.max": "magenta",
        "benchr.name": "magenta",
        "benchr.label": "bold",
        "benchr.better": "green bold",
        "benchr.worse": "red bold",
        "benchr.progress": "blue bold",
        "benchr.in_process": "magenta bold",
    }
)

console = Console(theme=BENCHR_THEME, highlight=False)
