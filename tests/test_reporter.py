"""Reporter sinks (Csv, Json, Dir, Mixed)."""

import json
from pathlib import Path

from benchr import (
    Csv, Dir, Json, Mixed, P, Sequential, bench, report_from_json, suite,
)


def _s():
    return suite(
        "S",
        bench("a")
            .with_command(["sh", "-c", "echo 1.5; echo 2.5"])
            .with_cwd(Path("/tmp"))
            .with_process(P.float_per_line("s").last_line().lower_is_better())
            .runs(2),
    )


def test_csv_writer(tmp_path: Path):
    out = tmp_path / "r.csv"
    Sequential(reporter=Csv(out)).run([_s()], ctx=None)
    text = out.read_text()
    lines = text.splitlines()
    assert lines[0].split(",")[:4] == ["suite", "benchmark", "run", "phase"]
    assert len(lines) == 3  # header + 2 rows


def test_json_writer_round_trip(tmp_path: Path):
    out = tmp_path / "r.json"
    Sequential(reporter=Json(out)).run([_s()], ctx=None)
    r = report_from_json(out.read_text())
    assert len(r.samples) == 2
    assert all(s.metric == "runtime" for s in r.samples)


def test_dir_writer_creates_tree(tmp_path: Path):
    root = tmp_path / "tree"
    Sequential(reporter=Dir(root)).run([_s()], ctx=None)
    files = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    expected_files = {"seq", "stdout", "stderr", "exitcode"}
    leaf_files = {f.name for f in files}
    assert expected_files <= leaf_files
    # one dir per run
    run_dirs = sorted({p.parent for p in files})
    assert len(run_dirs) == 2


def test_mixed_fans_out(tmp_path: Path):
    js = tmp_path / "r.json"
    cs = tmp_path / "r.csv"
    Sequential(reporter=Mixed(Json(js), Csv(cs))).run([_s()], ctx=None)
    assert js.exists() and cs.exists()


def test_csv_header_includes_info_columns(tmp_path: Path):
    out = tmp_path / "r.csv"
    s = (
        suite("M", bench("c"))
        .with_cwd(Path("/tmp")).with_process(P.time())
        .matrix("compiler", ["gcc"], command=lambda b, ctx, v: ["sh", "-c", "sleep 0.01"])
        .runs(1)
    )
    Sequential(reporter=Csv(out)).run([s], ctx=None)
    header = out.read_text().splitlines()[0]
    assert "compiler" in header.split(",")
