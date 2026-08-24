"""Environment metadata on the Report and in the reporter outputs."""

import json
from pathlib import Path

from bench.core.environment import Diagnostic, Environment
from bench.core.results import (
    Execution,
    Iteration,
    Report,
    Sample,
    report_from_json,
    report_to_json,
)
from bench.report.reporter import CsvReporter, DirReporter, JsonReporter


def _run() -> Execution:
    return Execution(
        suite="s",
        benchmark="b",
        runtime=1.0,
        iterations=[Iteration(samples=[Sample("elapsed", 1.0, "s", iteration=0)])],
    )


def test_report_environment_roundtrips():
    env = Environment(
        system="Linux", governors=["performance"], load_avg=[0.1, 0.2, 0.3]
    )
    rep = Report(
        executions=[], environment=env, diagnostics=[Diagnostic("high", "m", "f")]
    )
    back = report_from_json(report_to_json(rep))
    assert back.environment == env
    assert back.diagnostics[0].severity == "high"


def test_old_json_without_environment_loads():
    back = report_from_json('{"executions": []}')
    assert back.environment is None
    assert back.diagnostics == []


def test_json_reporter_embeds_environment(tmp_path: Path):
    env = Environment(system="Linux", cpu_model="X")
    r = JsonReporter(tmp_path / "o.json")
    r.finalize(
        Report(
            executions=[_run()],
            environment=env,
            diagnostics=[Diagnostic("warn", "m", "f")],
        )
    )
    data = json.loads((tmp_path / "o.json").read_text())
    assert data["environment"]["cpu_model"] == "X"
    assert data["diagnostics"][0]["message"] == "m"


def test_csv_reporter_writes_environment_comments(tmp_path: Path):
    env = Environment(system="Linux", cpu_model="X")
    r = CsvReporter(tmp_path / "o.csv")
    r.finalize(Report(executions=[_run()], environment=env))
    text = (tmp_path / "o.csv").read_text()
    assert text.splitlines()[0].startswith("#")
    assert "# cpu_model: X" in text
    assert "suite,benchmark,run" in text  # header still present


def test_dir_reporter_writes_environment_json(tmp_path: Path):
    env = Environment(system="Linux", cpu_model="X")
    r = DirReporter(tmp_path)
    r.start([])
    r.execution_done(_run())
    r.finalize(Report(executions=[_run()], environment=env))
    data = json.loads((tmp_path / "environment.json").read_text())
    assert data["environment"]["cpu_model"] == "X"


def test_dir_reporter_without_environment_writes_no_file(tmp_path: Path):
    r = DirReporter(tmp_path)
    r.start([])
    r.finalize(Report())
    assert not (tmp_path / "environment.json").exists()
