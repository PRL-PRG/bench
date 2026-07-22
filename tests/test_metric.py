"""Metric builtins and combinators.

An `IterationMetric` parses one iteration's text (`process_text`), while a
process `Metric` reads the whole `InvocationResult` (`process`). Iteration
metrics take a `MetricSource` (e.g. `StdoutMetricSource`) as their first
argument; the parsing tests exercise `process_text` directly.
"""

import re

from bench import (
    FloatPerLine,
    Rebench,
    Regex,
    Time,
    max_rss,
)
from bench.core.metric import (
    StderrMetricSource,
    StdoutMetricSource,
    SystemTime,
    UserTime,
    as_metric_source,
)

from conftest import make_success, make_rusage


# ----- iteration metrics (parse text) ---------------------------------------


def test_float_per_line_basic():
    samples = list(
        FloatPerLine(StdoutMetricSource, "runtime", unit="s").process_text("1.5\n2.5\n")
    )
    assert [s.value for s in samples] == [1.5, 2.5]
    assert all(s.unit == "s" and s.metric == "runtime" for s in samples)


def test_float_per_line_skips_garbage():
    samples = list(
        FloatPerLine(StdoutMetricSource, "runtime", unit="s").process_text(
            "garbage\n1.0\nmore\n2.0\n"
        )
    )
    assert [s.value for s in samples] == [1.0, 2.0]


def test_float_per_line_empty_text_emits_nothing():
    assert (
        list(FloatPerLine(StdoutMetricSource, "runtime", unit="s").process_text("")) == []
    )


def test_line_select_last_and_nth():
    text = "1\n2\n3\n"
    assert (
        list(
            FloatPerLine.last_line(StdoutMetricSource, "runtime", unit="s").process_text(
                text
            )
        )[0].value
        == 3
    )
    assert (
        list(
            FloatPerLine(
                StdoutMetricSource, "runtime", line=2, unit="s"
            ).process_text(text)
        )[0].value
        == 2
    )


def test_direction_decorator():
    proc = FloatPerLine(StdoutMetricSource, "runtime", unit="s").lower_is_better()
    assert next(iter(proc.process_text("1\n"))).lower_is_better is True
    proc = FloatPerLine(StdoutMetricSource, "runtime", unit="s").higher_is_better()
    assert next(iter(proc.process_text("1\n"))).lower_is_better is False


def test_regex_unit_in_pattern_or_arg():
    proc = Regex(
        "rt",
        re.compile(r"time:\s*([\d.]+)\s*(ms|us)"),
        StdoutMetricSource,
        match_group=1,
        unit_group=2,
    )
    samples = list(proc.process_text("time: 12.5 ms\ntime: 7 us"))
    assert samples[0].value == 12.5 and samples[0].unit == "ms"
    assert samples[1].value == 7.0 and samples[1].unit == "us"


def test_regex_unit_defaults_to_empty():
    samples = list(Regex("n", r"(\d+)", StdoutMetricSource).process_text("42\n"))
    assert samples[0].unit == ""


def test_rebench_metric():
    text = (
        "log: bench1 total: iterations=1 runtime: 1500ms\nlog: bench1: gc-rate: 12kB\n"
    )
    samples = list(Rebench(StdoutMetricSource).process_text(text))
    assert any(s.metric == "runtime" and s.unit == "ms" for s in samples)
    assert any(s.metric == "gc-rate" for s in samples)


# ----- process metrics (read InvocationResult) -------------------------------


def test_time_metric_emits_elapsed():
    pr = make_success(runtime=1.0)
    metrics = [s.metric for s in Time().process(pr)]
    assert metrics == ["elapsed"]


def test_user_and_system_time_metrics():
    ru = make_rusage(ru_utime=0.2, ru_stime=0.1)
    pr = make_success(runtime=1.0, rusage=ru)
    assert [s.metric for s in UserTime().process(pr)] == ["user"]
    assert [s.metric for s in SystemTime().process(pr)] == ["system"]


def test_max_rss():
    pr = make_success(rusage=make_rusage(ru_maxrss=10240))
    samples = list(max_rss().process(pr))
    assert samples[0].metric == "max_rss"
    assert samples[0].unit == "kB"


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
