"""Processor builtins and combinators."""

import re

import pytest

from benchr import (
    PartialSample, Processor, P, process_all, stamp,
)

from conftest import make_failure, make_rusage, make_sched, make_success


def test_float_per_line_basic():
    pr = make_success(stdout="1.5\n2.5\n")
    samples = list(P.float_per_line("s").process(pr))
    assert [s.value for s in samples] == [1.5, 2.5]
    assert all(s.unit == "s" and s.metric == "runtime" for s in samples)


def test_float_per_line_skips_garbage():
    pr = make_success(stdout="garbage\n1.0\nmore\n2.0\n")
    samples = list(P.float_per_line("s").process(pr))
    assert [s.value for s in samples] == [1.0, 2.0]


def test_float_per_line_failed_emits_nothing():
    samples = list(P.float_per_line("s").process(make_failure()))
    assert samples == []


def test_line_select_last_and_nth():
    pr = make_success(stdout="1\n2\n3\n")
    assert list(P.float_per_line("s").last_line().process(pr))[0].value == 3
    assert list(P.float_per_line("s").nth(2).process(pr))[0].value == 2
    assert list(P.float_per_line("s").first_line().process(pr))[0].value == 1


def test_process_all_concatenates():
    pr = make_success(stdout="1\n", runtime=0.5)
    samples = list(process_all([P.float_per_line("s"), P.time()], pr))
    metrics = {s.metric for s in samples}
    assert "runtime" in metrics and "elapsed" in metrics


def test_direction_decorator():
    pr = make_success(stdout="1\n")
    proc = P.float_per_line("s").lower_is_better()
    assert next(iter(proc.process(pr))).lower_is_better is True
    proc = P.float_per_line("s").higher_is_better()
    assert next(iter(proc.process(pr))).lower_is_better is False


def test_when_predicate():
    proc = P.constant("x", 1.0).when(lambda pr: pr.stdout == "yes\n")
    assert list(proc.process(make_success(stdout="yes\n")))
    assert not list(proc.process(make_success(stdout="no\n")))


def test_regex_unit_in_pattern_or_arg():
    pr = make_success(stdout="time: 12.5 ms\ntime: 7 us")
    proc = P.regex("rt", re.compile(r"time:\s*([\d.]+)\s*(ms|us)"),
                   match_group=1, unit_group=2)
    samples = list(proc.process(pr))
    assert samples[0].value == 12.5 and samples[0].unit == "ms"
    assert samples[1].value == 7.0 and samples[1].unit == "us"


def test_time_processor_emits_optional_fields():
    ru = make_rusage(ru_utime=0.2, ru_stime=0.1)
    pr = make_success(runtime=1.0, rusage=ru)
    proc = P.time(elapsed=True, user=True, system=True)
    metrics = [s.metric for s in proc.process(pr)]
    assert metrics == ["elapsed", "user", "system"]


def test_max_rss():
    ru = make_rusage(ru_maxrss=10240)
    pr = make_success(rusage=ru)
    samples = list(P.max_rss().process(pr))
    assert samples[0].metric == "max_rss"
    assert samples[0].unit == "kB"


def test_rebench_processor():
    pr = make_success(stdout=(
        "log: bench1 total: iterations=1 runtime: 1500ms\n"
        "log: bench1: gc-rate: 12kB\n"
    ))
    samples = list(P.rebench().process(pr))
    assert any(s.metric == "runtime" and s.unit == "ms" for s in samples)
    assert any(s.metric == "gc-rate" for s in samples)


def test_stamp():
    sched = make_sched(suite="X", benchmark="Y", run=2, phase="warmup")
    samples = list(stamp([PartialSample("m", 1.0, "u", True)], sched))
    s = samples[0]
    assert s.suite == "X" and s.benchmark == "Y" and s.run == 2 and s.phase == "warmup"
    assert s.metric == "m" and s.value == 1.0 and s.unit == "u" and s.lower_is_better is True
