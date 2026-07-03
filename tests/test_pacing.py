"""cooldown (SuiteBuilder/benchmark property) and shuffle (SuiteBuilder property)."""

import pytest

from bench import NoEnvironment, bench, bench_app, suite


def test_cooldown_defaults_zero():
    [bm] = suite("s", bench("b").with_command(["true"])).materialize(None)
    assert bm.cooldown == 0.0


def test_cooldown_inherits_from_suite():
    s = suite("s", bench("b").with_command(["true"])).with_cooldown(0.5)
    [bm] = s.materialize(None)
    assert bm.cooldown == 0.5


def test_cooldown_benchmark_overrides_suite():
    s = suite("s", bench("b").with_command(["true"]).with_cooldown(0.1)).with_cooldown(
        0.5
    )
    [bm] = s.materialize(None)
    assert bm.cooldown == 0.1


def test_cooldown_sleeps_between_runs(monkeypatch: pytest.MonkeyPatch):
    calls: list[float] = []
    monkeypatch.setattr("bench.runner.controller.time.sleep", lambda s: calls.append(s))
    s = suite("s", bench("b").with_command(["true"]).with_runs(3).with_cooldown(0.05))
    bench_app(environment=NoEnvironment()).add_all(s).run(["--no-progress"])
    assert calls == [0.05, 0.05]  # 3 runs -> 2 gaps, none before the first


def test_no_shuffle_preserves_order():
    names = [f"b{i}" for i in range(5)]
    s = suite("s", *(bench(n).with_command(["true"]) for n in names))
    assert [b.name for b in s.materialize(None)] == names


def test_shuffle_is_deterministic_and_reorders():
    names = [f"b{i}" for i in range(8)]

    def make():
        return suite(
            "s", *(bench(n).with_command(["true"]) for n in names)
        ).with_shuffle(seed=1)

    first = [b.name for b in make().materialize(None)]
    again = [b.name for b in make().materialize(None)]
    assert first == again  # same seed -> same order
    assert sorted(first) == names  # same set
    assert first != names  # actually reordered
