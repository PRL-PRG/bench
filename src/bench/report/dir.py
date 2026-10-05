from __future__ import annotations

import json
from pathlib import Path
from typing import overload

from cattrs import unstructure

from bench.model.benchmark import Benchmark
from bench.model.results import Execution, Report
from bench.report.base import Reporter


# TODO: Move somewhere else
@overload
def execution_dir(root: Path, execution: Execution, /, *, nested: bool) -> Path: ...
@overload
def execution_dir(
    root: Path, benchmark: Benchmark, run: int, /, *, nested: bool
) -> Path: ...
def execution_dir(
    root: Path,
    id: Execution | Benchmark,
    run: int | None = None,
    /,
    *,
    nested: bool,
) -> Path:
    if isinstance(id, Execution):
        run = id.run
        name = id.benchmark
    else:
        if run is None:
            raise ValueError("Run cannot be None when using Benchmark")
        name = id.name

    path = root / id.suite / name

    if nested:
        for k, v in id.variant:
            path /= f"{k}={v}"
    elif id.variant_label:
        path /= id.variant_label.replace("/", "_")
    elif id.variant:
        path /= ",".join(f"{k}={v}" for k, v in id.variant)

    path /= str(run)

    return path


class DirReporter(Reporter):
    """Per-execution tree at `<out>/<suite>/<bench>[/<variant>]/<run>/` (see
    `execution_dir`).

    Files: stdout, stderr, exitcode, seq (cwd + cmd + info). For a matrix variant
    `<variant>` is a single component - the variant label with `/` replaced by
    `_`, or `dim=val,...` when it has none - by default, or one `dim=val`
    directory per dimension when constructed with `nested=True`. A plain
    benchmark has no `<variant>` level.
    `<run>` is the execution's run number. `fingerprint.json` goes in `<out>`.
    """

    def __init__(
        self,
        root: Path,
        *,
        nested: bool = False,
    ) -> None:
        self.root = root
        self.nested = nested

    def start(self, plan: list[Benchmark]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def execution_done(self, execution: Execution) -> None:
        exec_dir = execution_dir(self.root, execution, nested=self.nested)
        exec_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            f"cwd={execution.cwd}",
            f"command={' '.join(execution.command)}",
            f"run={execution.run}",
        ]
        lines.extend(f"variant[{k}]={v}" for k, v in execution.variant)
        if execution.variant_label:
            lines.append(f"variant_label={execution.variant_label}")
        (exec_dir / "seq").write_text("\n".join(lines) + "\n")

        (exec_dir / "stdout").write_text(execution.stdout)
        (exec_dir / "stderr").write_text(execution.stderr)
        (exec_dir / "exitcode").write_text(f"{execution.returncode}\n")

    def finalize(self, report: Report) -> None:
        if report.fingerprint is not None:
            (self.root / "fingerprint.json").write_text(
                json.dumps(
                    {
                        "fingerprint": unstructure(report.fingerprint),
                        "diagnostics": unstructure(report.diagnostics),
                    },
                    indent=2,
                )
            )
