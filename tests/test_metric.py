"""Metric builtins and combinators.

`IterationMetric.process(text)` parses one iteration's text. `ProcessMetric.
process(result)` reads the whole InvocationResult.
"""

import re

from bench import (
    FloatPerLine,
    IterationMetric,
    ProcessMetric,
    RUsage,
    Rebench,
    Regex,
    Time,
    max_rss,
)
from bench.core.metric import (
    StderrMetricSource,
    StdoutMetricSource,
    as_metric_source,
)

from conftest import make_success, make_rusage


# ----- iteration metrics (parse text) ---------------------------------------


def test_float_per_line_basic():
    samples = list(FloatPerLine("s").process("1.5\n2.5\n"))
    assert [s.value for s in samples] == [1.5, 2.5]
    assert all(s.unit == "s" and s.metric == "runtime" for s in samples)


def test_float_per_line_skips_garbage():
    samples = list(FloatPerLine("s").process("garbage\n1.0\nmore\n2.0\n"))
    assert [s.value for s in samples] == [1.0, 2.0]


def test_float_per_line_empty_text_emits_nothing():
    assert list(FloatPerLine("s").process("")) == []


def test_line_select_last_and_nth():
    text = "1\n2\n3\n"
    assert list(FloatPerLine("s").last_line().process(text))[0].value == 3
    assert list(FloatPerLine("s").nth(2).process(text))[0].value == 2
    assert list(FloatPerLine("s").first_line().process(text))[0].value == 1


def test_direction_decorator():
    proc = FloatPerLine("s").lower_is_better()
    assert next(iter(proc.process("1\n"))).lower_is_better is True
    proc = FloatPerLine("s").higher_is_better()
    assert next(iter(proc.process("1\n"))).lower_is_better is False


def test_when_predicate():
    # .when() gates the whole metric on the iteration text: "2.0" would parse,
    # but the predicate is false, so it emits nothing.
    proc = FloatPerLine("s").when(lambda text: text == "1.0\n")
    assert list(proc.process("1.0\n"))
    assert not list(proc.process("2.0\n"))


def test_regex_unit_in_pattern_or_arg():
    proc = Regex(
        "rt", re.compile(r"time:\s*([\d.]+)\s*(ms|us)"), match_group=1, unit_group=2
    )
    samples = list(proc.process("time: 12.5 ms\ntime: 7 us"))
    assert samples[0].value == 12.5 and samples[0].unit == "ms"
    assert samples[1].value == 7.0 and samples[1].unit == "us"


def test_regex_unit_defaults_to_empty():
    samples = list(Regex("n", r"(\d+)").process("42\n"))
    assert samples[0].unit == ""


def test_rebench_metric():
    text = (
        "log: bench1 total: iterations=1 runtime: 1500ms\nlog: bench1: gc-rate: 12kB\n"
    )
    samples = list(Rebench().process(text))
    assert any(s.metric == "runtime" and s.unit == "ms" for s in samples)
    assert any(s.metric == "gc-rate" for s in samples)


# ----- process metrics (read InvocationResult) -------------------------------


def test_time_metric_emits_optional_fields():
    ru = make_rusage(ru_utime=0.2, ru_stime=0.1)
    pr = make_success(runtime=1.0, rusage=ru)
    metrics = [s.metric for s in Time(elapsed=True, user=True, system=True).process(pr)]
    assert metrics == ["elapsed", "user", "system"]


def test_max_rss():
    pr = make_success(rusage=make_rusage(ru_maxrss=10240))
    samples = list(max_rss().process(pr))
    assert samples[0].metric == "max_rss"
    assert samples[0].unit == "kB"


def test_process_metric_when_predicate():
    pr = make_success(runtime=1.0, rusage=make_rusage())
    assert list(Time().when(lambda r: r.returncode == 0).process(pr))
    assert not list(Time().when(lambda r: r.returncode != 0).process(pr))


# ----- the iteration/process distinction is the class, not a flag -----------


def test_builtin_metric_kinds():
    assert isinstance(Regex("t", r"(\d+)"), IterationMetric)
    assert isinstance(FloatPerLine(), IterationMetric)
    assert isinstance(Rebench(), IterationMetric)
    assert isinstance(Time(), ProcessMetric)
    assert isinstance(RUsage("ru_maxrss", "m"), ProcessMetric)
    assert isinstance(max_rss(), ProcessMetric)


def test_builder_methods_preserve_kind():
    assert isinstance(RUsage("ru_maxrss", "m").lower_is_better(), ProcessMetric)
    assert isinstance(Regex("t", r"(\d+)").lower_is_better(), IterationMetric)
    assert isinstance(Time().when(lambda r: True), ProcessMetric)
    assert isinstance(FloatPerLine().when(lambda text: True), IterationMetric)


# ----- metric sources -------------------------------------------------------


def test_metric_source_shorthands():
    pr = make_success(stdout="out", stderr="err")
    assert as_metric_source("stdout") is StdoutMetricSource
    assert as_metric_source("stderr") is StderrMetricSource
    assert StdoutMetricSource(pr) == "out"
    assert StderrMetricSource(pr) == "err"


def test_metric_source_callable_passthrough():
    src = as_metric_source(lambda r: (r.stdout or "").upper())
    assert src(make_success(stdout="hi")) == "HI"
