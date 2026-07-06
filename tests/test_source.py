"""ExecutionSource: CommandSource (pull) and HarnessSource (streaming push)."""

import sys
import threading
import time
from pathlib import Path

import pytest

from bench import FloatPerLine, Time, bench, suite
from bench.runner.base import plan
from bench.runner.source import HarnessSource, make_source


def _planned(cmd, metric):
    s = (
        suite("S", bench("b").with_command(cmd))
        .with_cwd(Path("/tmp"))
        .with_metric(metric)
        .with_runs(1)
    )
    return plan([s], None)[0]


def _planned_harness(cmd, metric):
    s = (
        suite("H", bench("a").with_command(cmd))
        .with_cwd(Path("/tmp"))
        .with_metric(metric)
        .with_runs(1)
        .with_harness()
    )
    return plan([s], None)[0]


def _drain(src):
    out = []
    while True:
        try:
            obs, _ = src.next()
        except StopIteration:
            break
        out.append(obs)
    return out


# ----- CommandSource ------------------------------------------------------


def test_command_source_yields_observation_with_all_metrics():
    src = make_source(_planned(["sh", "-c", "echo 1.5"], FloatPerLine("")))
    obs, label = src.next()
    assert obs.failure is None
    assert any(s.value == 1.5 for s in obs.samples)
    assert "S/b" in label  # carried for live progress
    src.close()


def test_command_source_failure_sets_verdict_and_no_samples():
    src = make_source(_planned(["sh", "-c", "exit 3"], FloatPerLine("")))
    obs, _ = src.next()
    assert obs.failure == "exit code 3" and obs.samples == []
    runs = src.close()
    assert (
        len(runs) == 1 and runs[0].returncode == 3 and runs[0].failure == "exit code 3"
    )


def test_command_source_process_metrics_go_to_process_samples():
    s = (
        suite("S", bench("b").with_command(["sh", "-c", "echo 1.5"]))
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(1)
    )
    src = make_source(plan([s], None)[0])
    it, _ = src.next()
    run = src.close()[0]
    # A command's process metrics live on the run, not its single iteration.
    assert all(s.metric != "elapsed" for s in it.samples)
    assert any(s.metric == "elapsed" for s in run.process_samples)


# ----- HarnessSource ------------------------------------------------------


def test_harness_source_streams_one_observation_per_line():
    src = make_source(
        _planned_harness(["sh", "-c", "echo 1.0; echo 2.0; echo 3.0"], FloatPerLine(""))
    )
    got = _drain(src)
    src.close()
    assert [o.samples[0].value for o in got] == [1.0, 2.0, 3.0]


def test_harness_source_close_kills_long_process():
    src = make_source(
        _planned_harness(["sh", "-c", "echo 1.0; sleep 30; echo 2.0"], FloatPerLine(""))
    )
    first, _ = src.next()  # blocks until the first line
    assert first.samples[0].value == 1.0
    t = time.monotonic()
    src.close()  # must not wait for sleep 30
    assert time.monotonic() - t < 5


def test_harness_process_metrics_go_to_process_samples():
    s = (
        suite("H", bench("a").with_command(["sh", "-c", "echo 1.0; echo 2.0"]))
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine(""))
        .with_process_metric(Time())
        .with_runs(2)
        .with_harness()
    )
    src = make_source(plan([s], None)[0])
    _drain(src)
    run = src.close()[0]
    # FloatPerLine -> per-iteration samples. Time -> whole-process samples.
    assert all(s.metric != "elapsed" for it in run.iterations for s in it.samples)
    assert any(s.metric == "elapsed" for s in run.process_samples)


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
        _planned_harness(["definitely-not-a-real-command-xyz"], FloatPerLine(""))
    )
    with pytest.raises(StopIteration):
        src.next()
    runs = src.close()
    assert len(runs) == 1 and runs[0].is_failure()


def test_harness_source_clean_exit_run_succeeds():
    src = make_source(
        _planned_harness(["sh", "-c", "echo 1.0; echo 2.0; exit 0"], FloatPerLine(""))
    )
    _drain(src)
    run = src.close()[0]
    assert run.returncode == 0 and run.failure is None


def test_harness_child_runs_unbuffered_by_default(monkeypatch):
    # A harness streams per-iteration lines, so its (Python) child must not
    # block-buffer stdout or every line arrives at once when it exits.
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    child = [
        sys.executable,
        "-c",
        "import os; print(os.environ.get('PYTHONUNBUFFERED', 'UNSET'))",
    ]
    src = make_source(_planned_harness(child, FloatPerLine("")))
    _drain(src)
    run = src.close()[0]
    assert run.stdout.strip() == "1"


def test_harness_child_unbuffered_flag_preserves_user_env():
    # A non-empty env replaces the parent env wholesale, so the unbuffered flag
    # must be merged in without clobbering the user's variables. (Immune to the
    # test runner's own PYTHONUNBUFFERED for the same reason.)
    child = [
        sys.executable,
        "-c",
        "import os; print(os.environ.get('PYTHONUNBUFFERED', 'UNSET')); "
        "print(os.environ.get('FOO', 'UNSET'))",
    ]
    s = (
        suite("H", bench("a").with_command(child))
        .with_cwd(Path("/tmp"))
        .with_env({"FOO": "bar"})
        .with_metric(FloatPerLine(""))
        .with_runs(1)
        .with_harness()
    )
    src = make_source(plan([s], None)[0])
    _drain(src)
    run = src.close()[0]
    assert run.stdout.split() == ["1", "bar"]


def test_harness_source_temp_dir_cleaned_up_after_run():
    src = make_source(
        _planned_harness(["sh", "-c", "echo 1.0; echo 2.0"], FloatPerLine(""))
    )
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
    assert stop_iteration_seen.is_set(), (
        "_DONE was not delivered; next() would have hung"
    )
    live.finish = original_finish  # restore so close() can reap cleanly
    src.close()
