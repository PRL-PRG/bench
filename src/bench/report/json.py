from __future__ import annotations

from pathlib import Path

from bench.model.results import Report, report_to_json
from bench.report.base import Reporter


class JsonReporter(Reporter):
    """Buffer runs in memory, write a single JSON file on finalize().

    `include_output` keeps each run's stdout/stderr/env in the JSON (off by
    default, they bloat the file and are rarely needed offline)."""

    def __init__(
        self,
        path: Path,
        *,
        include_output: bool = False,
    ) -> None:
        super().__init__()
        self.path = path
        self.include_output = include_output

    def finalize(self, report: Report) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(report_to_json(report, include_output=self.include_output))
