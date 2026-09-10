"""Dataclass -> argparse glue, and the Context value object."""

import argparse
from dataclasses import field
from pathlib import Path
from typing import Any

import pytest

from bench.builder import Context, Data
from bench.params import (
    Params,
    ParamsGroup,
    SharedBenchParams,
    SharedReporterParams,
    SharedRunnerParams,
    SharedSelectionParams,
    add_params,
    build_params,
)


class _Params(Params):
    name: Path  # required (no default)
    iterations: int = 15
    cwd: Path = Path("/tmp")
    verbose: bool = False
    label: str | None = None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    add_params(p, _Params)
    return p


def test_required_field_missing():
    with pytest.raises(SystemExit):
        _parser().parse_args([])


def test_required_field_only():
    ctx = build_params(_parser().parse_args(["--name", "/x"]), _Params)
    assert ctx.name == Path("/x")
    assert ctx.iterations == 15
    assert ctx.cwd == Path("/tmp")
    assert ctx.verbose is False
    assert ctx.label is None


def test_overrides():
    ns = _parser().parse_args(
        [
            "--name",
            "/x",
            "--iterations",
            "30",
            "--cwd",
            "/y",
            "--verbose",
            "--label",
            "tag",
        ]
    )
    ctx = build_params(ns, _Params)
    assert ctx.iterations == 30
    assert ctx.cwd == Path("/y")
    assert ctx.verbose is True
    assert ctx.label == "tag"


def test_bool_uses_boolean_optional_action():
    ns = _parser().parse_args(["--name", "/x", "--no-verbose"])
    ctx = build_params(ns, _Params)
    assert ctx.verbose is False


def test_dash_to_underscore():
    class Multi(Params):
        my_long_name: str = "x"

    p = argparse.ArgumentParser()
    add_params(p, Multi)
    ns = p.parse_args(["--my-long-name", "y"])
    ctx = build_params(ns, Multi)
    assert ctx.my_long_name == "y"


# ----- Context value object -----------------------------------------------


def _ctx(**overrides: Any) -> Context[Params]:
    base: dict[str, Any] = dict(
        params=None,
        suite="S",
        benchmark="b",
        data=Data(),
    )
    base.update(overrides)
    return Context(**base)


def test_context_data_attribute_access():
    # Variant values are read as attributes (ctx.data.vm), a missing one raises.
    ctx = _ctx(data=Data({"vm": "v8", "size": 100}))
    assert ctx.data.vm == "v8" and ctx.data.size == 100
    with pytest.raises(AttributeError):
        _ = ctx.data.nope


def test_context_suite_level_has_no_benchmark_or_data():
    # At suite level (factories) benchmark is None and the data is empty.
    ctx = _ctx(benchmark=None, data=Data())
    assert ctx.benchmark is None
    with pytest.raises(AttributeError):
        _ = ctx.data.vm


# ----- add_params extensions --------------------------------------


def test_list_field_is_repeatable_append():
    class DC(Params):
        tags: list[str] | None = None

    p = argparse.ArgumentParser()
    add_params(p, DC)
    # Repeatable, and a value is kept whole (not char-split).
    ns = p.parse_args(["--tags", "^a$", "--tags", "b"])
    assert build_params(ns, DC).tags == ["^a$", "b"]
    # Omitted -> None.
    assert build_params(p.parse_args([]), DC).tags is None


def test_metadata_short_flag_alias():
    ns = _shared_parser().parse_args(["-j", "4"])
    assert build_params(ns, SharedBenchParams).jobs == 4


def test_metadata_help_and_metavar_surface_in_help():
    help_text = _shared_parser().format_help()
    assert "Run up to N benchmarks in parallel" in help_text
    assert "REGEX" in help_text  # include/exclude metavar


def test_positional_metadata_makes_an_argument_positional():
    class DC(Params):
        file: str = field(metadata={"positional": True, "help": "a report"})
        metric: str | None = None

    p = argparse.ArgumentParser()
    add_params(p, DC)
    assert build_params(p.parse_args(["r.json"]), DC).file == "r.json"
    # A positional with no default is required, and never gets a `--` flag.
    assert "--file" not in p.format_help()
    with pytest.raises(SystemExit):
        p.parse_args([])


def test_list_positional_takes_every_remaining_value():
    class DC(Params):
        commands: list[str] = field(metadata={"positional": True, "metavar": "CMD"})

    p = argparse.ArgumentParser()
    add_params(p, DC)
    # nargs="+", not the repeatable `append` a `list[T]` option would get.
    assert build_params(p.parse_args(["a", "b"]), DC).commands == ["a", "b"]
    with pytest.raises(SystemExit):
        p.parse_args([])


def test_group_metadata_files_the_argument_under_its_group():
    class DC(Params):
        runs: int = field(default=1, metadata={"group": ParamsGroup("control")})
        loose: int = 0

    help_text = _params_parser(DC).format_help()
    assert "control:" in help_text
    group_section = help_text.split("control:", 1)[1]
    assert "--runs" in group_section
    # An ungrouped field stays in the parser's own section.
    assert "--loose" not in group_section


def test_shared_bench_params_defaults_and_progress():
    ns = _shared_parser().parse_args([])
    cli = build_params(ns, SharedBenchParams)
    assert cli.jobs == 1 and cli.dry is False and cli.verbose is False
    assert cli.progress is True and cli.include is None
    # --no-progress flips the progress default off.
    off = build_params(
        _shared_parser().parse_args(["--no-progress"]), SharedBenchParams
    )
    assert off.progress is False


def _shared_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    add_params(p, SharedBenchParams)
    return p


# ----- the shared params halves -------------------------------------------


def _params_parser(params: type[Params]) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    add_params(p, params)
    return p


def _flags(params: type[Params]) -> set[str]:
    return {
        opt
        for action in _params_parser(params)._actions
        for opt in action.option_strings
        if opt.startswith("--")
    } - {"--help"}


def test_each_shared_half_contributes_only_its_own_flags():
    # The three halves are independent opt-ins: a params class that only wants
    # `-j` must not also acquire `--json` or `--include`.
    assert _flags(SharedRunnerParams) == {"--jobs", "--dry", "--verbose"}
    assert _flags(SharedReporterParams) == {
        "--progress",
        "--no-progress",
        "--json",
        "--csv",
        "--dir",
    }
    assert _flags(SharedSelectionParams) == {"--include", "--exclude"}


def test_shared_bench_params_is_the_union_of_the_halves():
    # SharedBenchParams composes all three by multiple inheritance, which only
    # works because Params is not slotted (slotted bases collide in layout).
    halves = SharedRunnerParams, SharedReporterParams, SharedSelectionParams
    assert all(issubclass(SharedBenchParams, h) for h in halves)
    assert set(SharedBenchParams.__dataclass_fields__) == {
        f for h in halves for f in h.__dataclass_fields__
    }
    assert _flags(SharedBenchParams) == {f for h in halves for f in _flags(h)}


def test_a_user_params_class_can_pick_one_half():
    class RunnerOnly(SharedRunnerParams):
        label: str = "x"

    p = build_params(_params_parser(RunnerOnly).parse_args(["--jobs", "4"]), RunnerOnly)
    assert p.jobs == 4 and p.label == "x"
    assert not hasattr(p, "progress")
