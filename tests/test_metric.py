"""Metric builtins and combinators."""

import re

from benchr import Constant, FloatPerLine, Rebench, Regex, Time, max_rss

from conftest import make_failure, make_rusage, make_success


def test_float_per_line_basic():
    pr = make_success(stdout="1.5\n2.5\n")
    samples = list(FloatPerLine("s").process(pr))
    assert [s.value for s in samples] == [1.5, 2.5]
    assert all(s.unit == "s" and s.metric == "runtime" for s in samples)


def test_float_per_line_skips_garbage():
    pr = make_success(stdout="garbage\n1.0\nmore\n2.0\n")
    samples = list(FloatPerLine("s").process(pr))
    assert [s.value for s in samples] == [1.0, 2.0]


def test_float_per_line_failed_emits_nothing():
    samples = list(FloatPerLine("s").process(make_failure()))
    assert samples == []


def test_line_select_last_and_nth():
    pr = make_success(stdout="1\n2\n3\n")
    assert list(FloatPerLine("s").last_line().process(pr))[0].value == 3
    assert list(FloatPerLine("s").nth(2).process(pr))[0].value == 2
    assert list(FloatPerLine("s").first_line().process(pr))[0].value == 1


def test_direction_decorator():
    pr = make_success(stdout="1\n")
    proc = FloatPerLine("s").lower_is_better()
    assert next(iter(proc.process(pr))).lower_is_better is True
    proc = FloatPerLine("s").higher_is_better()
    assert next(iter(proc.process(pr))).lower_is_better is False


def test_when_predicate():
    proc = Constant("x", 1.0).when(lambda pr: pr.stdout == "yes\n")
    assert list(proc.process(make_success(stdout="yes\n")))
    assert not list(proc.process(make_success(stdout="no\n")))


def test_regex_unit_in_pattern_or_arg():
    pr = make_success(stdout="time: 12.5 ms\ntime: 7 us")
    proc = Regex("rt", re.compile(r"time:\s*([\d.]+)\s*(ms|us)"),
                   match_group=1, unit_group=2)
    samples = list(proc.process(pr))
    assert samples[0].value == 12.5 and samples[0].unit == "ms"
    assert samples[1].value == 7.0 and samples[1].unit == "us"


def test_time_metric_emits_optional_fields():
    ru = make_rusage(ru_utime=0.2, ru_stime=0.1)
    pr = make_success(runtime=1.0, rusage=ru)
    proc = Time(elapsed=True, user=True, system=True)
    metrics = [s.metric for s in proc.process(pr)]
    assert metrics == ["elapsed", "user", "system"]


def test_max_rss():
    ru = make_rusage(ru_maxrss=10240)
    pr = make_success(rusage=ru)
    samples = list(max_rss().process(pr))
    assert samples[0].metric == "max_rss"
    assert samples[0].unit == "kB"


def test_rebench_metric():
    pr = make_success(stdout=(
        "log: bench1 total: iterations=1 runtime: 1500ms\n"
        "log: bench1: gc-rate: 12kB\n"
    ))
    samples = list(Rebench().process(pr))
    assert any(s.metric == "runtime" and s.unit == "ms" for s in samples)
    assert any(s.metric == "gc-rate" for s in samples)


def test_regex_unit_defaults_to_empty():
    m = Regex("n", r"(\d+)")
    samples = list(m.process(make_success(stdout="42\n")))
    assert samples[0].unit == ""


# Task 1: RunMetric / ProcessMetric kinds + role-preserving combinators
from benchr import RunMetric, ProcessMetric, RUsage, FloatPerLine, Rebench
from benchr.core.metric import partition_metrics


def test_builtin_metric_kinds():
    assert isinstance(Regex("t", r"(\d+)"), RunMetric)
    assert isinstance(FloatPerLine(), RunMetric)
    assert isinstance(Rebench(), RunMetric)
    assert isinstance(Time(), ProcessMetric)
    assert isinstance(RUsage("ru_maxrss", "m"), ProcessMetric)


def test_lower_is_better_preserves_role():
    assert isinstance(RUsage("ru_maxrss", "m").lower_is_better(), ProcessMetric)
    assert isinstance(Regex("t", r"(\d+)").lower_is_better(), RunMetric)
    assert isinstance(max_rss(), ProcessMetric)


def test_when_preserves_role():
    assert isinstance(Time().when(lambda r: True), ProcessMetric)
    assert isinstance(FloatPerLine().when(lambda r: True), RunMetric)


def test_partition_metrics():
    run, proc = partition_metrics([Regex("t", r"(\d+)"), Time(), max_rss()])
    assert len(run) == 1 and len(proc) == 2


# Task 2: extract_run / extract_process
def test_extract_run_and_process_filter_by_kind():
    from benchr.core.metric import extract_run, extract_process
    from benchr import ExecutionResult, Execution
    from pathlib import Path
    res = ExecutionResult(execution=Execution(command=("x",), cwd=Path("/")),
                          returncode=0, stdout="3.0\n", runtime=1.5,
                          rusage=None)
    runs = list(extract_run([FloatPerLine(), Time()], res))
    procs = list(extract_process([FloatPerLine(), Time()], res))
    assert [s.metric for s in runs] == ["runtime"]      # FloatPerLine only
    assert [s.metric for s in procs] == ["elapsed"]      # Time only
