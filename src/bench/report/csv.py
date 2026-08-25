from __future__ import annotations

import csv
import itertools
from pathlib import Path
from typing import Any

from bench.core.fingerprint import Fingerprint
from bench.model.results import Report
from bench.report.base import Reporter


def _fingerprint_comments(fingerprint: Fingerprint | None) -> list[str]:
    """`# key: value` lines for each known field, for a CSV preamble."""
    if fingerprint is None:
        return []
    return [f"# {k}: {v}\n" for k, v in fingerprint.display_items()]


class CsvReporter(Reporter):
    """Buffer runs, write CSV on `finalize()`.

    Schema: `suite, benchmark, run, <variant_cols...>, metric, value, unit,
    lower_is_better, outlier, failure`. One row per Sample, for each iteration's samples
    and then the run's whole-process samples. A failed iteration (or run) emits
    one row with blank metric and the failure verdict. All runs appear, warmup
    included.
    """

    def __init__(
        self,
        path: Path,
        *,
        delimiter: str = ",",
    ) -> None:
        super().__init__()
        self.path = path
        self.delimiter = delimiter

    def finalize(self, report: Report) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        variant_cols = report.variant_keys()
        samples_extra = report.samples_extra_keys()
        cols = (
            ["suite", "benchmark", "run"]
            + variant_cols
            + ["failure", "iteration", "metric", "value", "unit", "lower_is_better"]
            + samples_extra
        )

        with open(self.path, "wt", newline="") as f:
            for line in _fingerprint_comments(report.fingerprint):
                f.write(line)
            w = csv.DictWriter(f, fieldnames=cols, delimiter=self.delimiter)
            w.writeheader()

            for e in report.executions:
                base: dict[str, Any] = {
                    "suite": e.suite,
                    "benchmark": e.benchmark,
                    "run": e.run,
                    "failure": e.failure or "",
                }
                for k in variant_cols:
                    base[k] = e.variant.get(k, "")

                w.writerow(
                    base
                    | {
                        "metric": "elapsed",
                        "value": str(e.runtime),
                        "unit": "s",
                    }
                )

                for sample in itertools.chain(
                    e.process_samples, (s for i in e.iterations for s in i.samples)
                ):
                    w.writerow(
                        base
                        | {
                            "iteration": sample.iteration
                            if sample.iteration is not None
                            else "",
                            "metric": sample.metric,
                            "value": sample.value,
                            "unit": sample.unit,
                            "lower_is_better": True
                            if sample.direction == "lower better"
                            else False
                            if sample.direction == "higher better"
                            else "",
                        }
                        | {name: sample.extra.get(name, "") for name in samples_extra}
                    )
