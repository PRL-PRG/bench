"""RunSource: CommandSource (pull) and HarnessSource (streaming push)."""

import pytest
from pathlib import Path

from benchr import Time, FloatPerLine, bench, suite, plan
from benchr.runner.source import CommandSource, make_source, RunSource


def _planned(cmd, metric):
    s = (suite("S", bench("b").with_command(cmd))
         .with_cwd(Path("/tmp")).with_metric(metric).with_runs(1))
    return plan([s], None)[0]


def _planned_harness(cmd, metric):
    s = (suite("H", bench("a").with_command(cmd))
         .with_cwd(Path("/tmp")).with_metric(metric).with_runs(1).with_harness())
    return plan([s], None)[0]


# ----- CommandSource ------------------------------------------------------


def test_command_source_yields_run_result_with_all_metrics():
    p = _planned(["sh", "-c", "echo 1.5"], FloatPerLine(""))
    src = make_source(p, None)
    rr = src.next()
    assert rr.failure is None
    assert any(s.value == 1.5 for s in rr.samples)        # RunMetric
    src.close()


def test_command_source_failure_sets_verdict_and_no_samples():
    p = _planned(["sh", "-c", "exit 3"], FloatPerLine(""))
    src = make_source(p, None)
    rr = src.next()
    assert rr.failure == "exit code 3" and rr.samples == []
    events = src.drain_process_events()
    assert len(events) == 1 and events[0][1].returncode == 3
    src.close()


def test_command_source_metadata_is_empty():
    p = _planned(["sh", "-c", "echo 1"], Time())
    src = make_source(p, None)
    src.next()
    assert src.metadata() == []          # command: process metrics fold into run samples
    src.close()


def test_command_source_process_metrics_fold_into_run_samples():
    p = _planned(["sh", "-c", "echo 1.5"], Time())
    src = make_source(p, None)
    rr = src.next()
    assert any(s.metric == "elapsed" for s in rr.samples)  # ProcessMetric folds in
    src.close()


def test_command_source_is_a_run_source():
    p = _planned(["sh", "-c", "echo 1.5"], FloatPerLine(""))
    assert isinstance(make_source(p, None), (CommandSource, RunSource))


# ----- HarnessSource ------------------------------------------------------


def test_harness_source_streams_one_run_per_line():
    p = _planned_harness(["sh", "-c", "echo 1.0; echo 2.0; echo 3.0"], FloatPerLine(""))
    src = make_source(p, None)
    got = []
    while True:
        try:
            got.append(src.next())
        except StopIteration:
            break
    src.close()
    assert [r.samples[0].value for r in got] == [1.0, 2.0, 3.0]


def test_harness_source_close_kills_long_process():
    import time
    p = _planned_harness(["sh", "-c", "echo 1.0; sleep 30; echo 2.0"], FloatPerLine(""))
    src = make_source(p, None)
    first = src.next()          # blocks until the first line
    assert first.samples[0].value == 1.0
    t = time.monotonic()
    src.close()                  # must not wait for sleep 30
    assert time.monotonic() - t < 5


def test_harness_source_process_metrics_become_metadata():
    s = (suite("H", bench("a").with_command(["sh", "-c", "echo 1.0; echo 2.0"]))
         .with_cwd(Path("/tmp"))
         .with_metric(FloatPerLine(""), Time())
         .with_runs(2).with_harness())
    p = plan([s], None)[0]
    src = make_source(p, None)
    while True:
        try:
            src.next()
        except StopIteration:
            break
    md = src.metadata()
    assert any(s.metric == "elapsed" for s in md)   # Time -> metadata, not per-iteration


def test_harness_source_non_parsing_line_yields_no_runs():
    # A line that parses to zero samples is not a measured iteration.
    p = _planned_harness(["sh", "-c", "echo hello"], FloatPerLine(""))
    src = make_source(p, None)
    got = []
    while True:
        try:
            got.append(src.next())
        except StopIteration:
            break
    src.close()
    assert got == []


def test_harness_source_nonzero_exit_sets_process_failure():
    p = _planned_harness(["sh", "-c", "exit 3"], FloatPerLine(""))
    src = make_source(p, None)
    while True:
        try:
            src.next()
        except StopIteration:
            break
    pr = src.process_result()
    assert pr is not None and pr.failure == "exit code 3"
    src.close()


def test_harness_source_spawn_failure_is_one_failed_event():
    p = _planned_harness(["definitely-not-a-real-command-xyz"], FloatPerLine(""))
    src = make_source(p, None)
    import pytest
    with pytest.raises(StopIteration):
        src.next()
    events = src.drain_process_events()
    assert len(events) == 1 and events[0][1].is_failure()
    src.close()


def test_harness_source_process_result_after_exhaustion():
    p = _planned_harness(["sh", "-c", "echo 1.0; echo 2.0; exit 0"], FloatPerLine(""))
    src = make_source(p, None)
    while True:
        try:
            src.next()
        except StopIteration:
            break
    pr = src.process_result()
    assert pr is not None and pr.returncode == 0
    src.close()


def test_harness_source_temp_dir_cleaned_up_after_run():
    p = _planned_harness(["sh", "-c", "echo 1.0; echo 2.0"], FloatPerLine(""))
    src = make_source(p, None)
    # Capture the temp dir before draining — _live exists at this point.
    tmp_dir = src._live.stdout_path.parent
    while True:
        try:
            src.next()
        except StopIteration:
            break
    src.close()
    assert not tmp_dir.exists(), f"temp dir was not cleaned up: {tmp_dir}"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_harness_source_done_delivered_even_if_finish_raises():
    """_DONE must reach the queue even when _live.finish() raises, so next()
    raises StopIteration rather than hanging forever."""
    import threading

    p = _planned_harness(["sh", "-c", "echo 1.0"], FloatPerLine(""))
    src = make_source(p, None)

    # Wait for the reader thread to start and the process to be live.
    # Monkeypatch finish() to raise after the reader thread is already running.
    original_finish = src._live.finish

    def _raising_finish(**kwargs):
        raise RuntimeError("injected finish failure")

    src._live.finish = _raising_finish

    results = []
    stop_iteration_seen = threading.Event()

    def drain():
        while True:
            try:
                src.next()
            except StopIteration:
                stop_iteration_seen.set()
                return

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    t.join(timeout=5)
    assert stop_iteration_seen.is_set(), "_DONE was not delivered; next() would have hung"
    src._live.finish = original_finish  # restore so close() can reap cleanly
    src.close()
