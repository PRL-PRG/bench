"""RunSource: CommandSource (pull) and HarnessSource (streaming push)."""

import threading
import time
from pathlib import Path

import pytest

from benchr import FloatPerLine, Time, bench, plan, suite
from benchr.runner.source import HarnessSource, make_source


def _planned(cmd, metric):
    s = (suite("S", bench("b").with_command(cmd))
         .with_cwd(Path("/tmp")).with_metric(metric).with_runs(1))
    return plan([s], None)[0]


def _planned_harness(cmd, metric):
    s = (suite("H", bench("a").with_command(cmd))
         .with_cwd(Path("/tmp")).with_metric(metric).with_runs(1).with_harness())
    return plan([s], None)[0]


def _drain(src):
    out = []
    while True:
        try:
            out.append(src.next())
        except StopIteration:
            break
    return out


# ----- CommandSource ------------------------------------------------------


def test_command_source_yields_observation_with_all_metrics():
    src = make_source(_planned(["sh", "-c", "echo 1.5"], FloatPerLine("")))
    obs = src.next()
    assert obs.failure is None
    assert any(s.value == 1.5 for s in obs.samples)
    assert "S/b" in obs.label   # carried for live progress
    src.close()


def test_command_source_failure_sets_verdict_and_no_samples():
    src = make_source(_planned(["sh", "-c", "exit 3"], FloatPerLine("")))
    obs = src.next()
    assert obs.failure == "exit code 3" and obs.samples == []
    runs = src.close()
    assert len(runs) == 1 and runs[0].returncode == 3 and runs[0].failure == "exit code 3"


def test_command_source_process_metrics_fold_into_one_observation():
    src = make_source(_planned(["sh", "-c", "echo 1.5"], Time()))
    obs = src.next()
    # Time (process metric) folds into the command's single observation.
    assert any(s.metric == "elapsed" for s in obs.samples)
    src.close()


# ----- HarnessSource ------------------------------------------------------


def test_harness_source_streams_one_observation_per_line():
    src = make_source(
        _planned_harness(["sh", "-c", "echo 1.0; echo 2.0; echo 3.0"], FloatPerLine("")))
    got = _drain(src)
    src.close()
    assert [o.samples[0].value for o in got] == [1.0, 2.0, 3.0]


def test_harness_source_close_kills_long_process():
    src = make_source(
        _planned_harness(["sh", "-c", "echo 1.0; sleep 30; echo 2.0"], FloatPerLine("")))
    first = src.next()          # blocks until the first line
    assert first.samples[0].value == 1.0
    t = time.monotonic()
    src.close()                  # must not wait for sleep 30
    assert time.monotonic() - t < 5


def test_harness_process_metrics_become_trailing_observation():
    s = (suite("H", bench("a").with_command(["sh", "-c", "echo 1.0; echo 2.0"]))
         .with_cwd(Path("/tmp"))
         .with_metric(FloatPerLine(""), Time())
         .with_runs(2).with_harness())
    src = make_source(plan([s], None)[0])
    _drain(src)
    run = src.close()[0]
    # FloatPerLine -> 2 per-iteration observations; Time -> a trailing
    # whole-process observation (no separate metadata).
    all_metrics = [sm.metric for o in run.observations for sm in o.samples]
    assert "elapsed" in all_metrics


def test_harness_source_non_parsing_line_yields_no_observation():
    # A line that parses to zero samples is not a measured iteration.
    src = make_source(_planned_harness(["sh", "-c", "echo hello"], FloatPerLine("")))
    got = _drain(src)
    src.close()
    assert got == []


def test_harness_source_nonzero_exit_sets_run_failure():
    src = make_source(_planned_harness(["sh", "-c", "exit 3"], FloatPerLine("")))
    _drain(src)
    run = src.close()[0]
    assert run.failure == "exit code 3" and run.returncode == 3


def test_harness_source_spawn_failure_is_one_failed_run():
    src = make_source(
        _planned_harness(["definitely-not-a-real-command-xyz"], FloatPerLine("")))
    with pytest.raises(StopIteration):
        src.next()
    runs = src.close()
    assert len(runs) == 1 and runs[0].is_failure()


def test_harness_source_clean_exit_run_succeeds():
    src = make_source(
        _planned_harness(["sh", "-c", "echo 1.0; echo 2.0; exit 0"], FloatPerLine("")))
    _drain(src)
    run = src.close()[0]
    assert run.returncode == 0 and run.failure is None


def test_harness_source_temp_dir_cleaned_up_after_run():
    src = make_source(
        _planned_harness(["sh", "-c", "echo 1.0; echo 2.0"], FloatPerLine("")))
    assert isinstance(src, HarnessSource)
    assert src._live is not None
    tmp_dir = src._live.stdout_path.parent
    _drain(src)
    src.close()
    assert not tmp_dir.exists(), f"temp dir was not cleaned up: {tmp_dir}"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_harness_source_done_delivered_even_if_finish_raises():
    """_DONE must reach the queue even when _live.finish() raises, so next()
    raises StopIteration rather than hanging forever."""
    src = make_source(_planned_harness(["sh", "-c", "echo 1.0"], FloatPerLine("")))
    assert isinstance(src, HarnessSource)
    live = src._live
    assert live is not None

    original_finish = live.finish

    def _raising_finish(**kwargs):
        raise RuntimeError("injected finish failure")

    live.finish = _raising_finish

    stop_iteration_seen = threading.Event()

    def drain():
        try:
            while True:
                src.next()
        except StopIteration:
            stop_iteration_seen.set()

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    t.join(timeout=5)
    assert stop_iteration_seen.is_set(), "_DONE was not delivered; next() would have hung"
    live.finish = original_finish  # restore so close() can reap cleanly
    src.close()
