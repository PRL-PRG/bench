"""Dataclass -> argparse glue, and the Context value object."""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bench.builder.context import (
    Context,
    Data,
    SharedBenchParams,
    add_dataclass_args,
    build_dataclass,
)


@dataclass
class _Params:
    name: Path  # required (no default)
    iterations: int = 15
    cwd: Path = Path("/tmp")
    verbose: bool = False
    label: str | None = None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    add_dataclass_args(p, _Params)
    return p


def test_required_field_missing():
    with pytest.raises(SystemExit):
        _parser().parse_args([])


def test_required_field_only():
    ctx = build_dataclass(_Params, _parser().parse_args(["--name", "/x"]))
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
    ctx = build_dataclass(_Params, ns)
    assert ctx.iterations == 30
    assert ctx.cwd == Path("/y")
    assert ctx.verbose is True
    assert ctx.label == "tag"


def test_bool_uses_boolean_optional_action():
    ns = _parser().parse_args(["--name", "/x", "--no-verbose"])
    ctx = build_dataclass(_Params, ns)
    assert ctx.verbose is False


def test_dash_to_underscore():
    @dataclass
    class Multi:
        my_long_name: str = "x"

    p = argparse.ArgumentParser()
    add_dataclass_args(p, Multi)
    ns = p.parse_args(["--my-long-name", "y"])
    ctx = build_dataclass(Multi, ns)
    assert ctx.my_long_name == "y"


# ----- Context value object -----------------------------------------------


def _ctx(**overrides: Any) -> Context[Any]:
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


# ----- add_dataclass_args extensions --------------------------------------


def test_list_field_is_repeatable_append():
    @dataclass
    class DC:
        tags: list[str] | None = None

    p = argparse.ArgumentParser()
    add_dataclass_args(p, DC)
    # Repeatable, and a value is kept whole (not char-split).
    ns = p.parse_args(["--tags", "^a$", "--tags", "b"])
    assert build_dataclass(DC, ns).tags == ["^a$", "b"]
    # Omitted -> None.
    assert build_dataclass(DC, p.parse_args([])).tags is None


def test_metadata_short_flag_alias():
    ns = _shared_parser().parse_args(["-j", "4"])
    assert build_dataclass(SharedBenchParams, ns).jobs == 4


def test_metadata_help_and_metavar_surface_in_help():
    help_text = _shared_parser().format_help()
    assert "Run up to N benchmarks in parallel" in help_text
    assert "REGEX" in help_text  # include/exclude metavar


def test_skip_omits_fields():
    p = argparse.ArgumentParser()
    add_dataclass_args(p, SharedBenchParams, skip={"include", "exclude"})
    assert "--include" not in p.format_help()
    # A non-skipped flag is still present.
    assert "--jobs" in p.format_help()


def test_shared_bench_params_defaults_and_progress():
    ns = _shared_parser().parse_args([])
    cli = build_dataclass(SharedBenchParams, ns)
    assert cli.jobs == 1 and cli.dry is False and cli.verbose is False
    assert cli.progress is True and cli.include is None
    # --no-progress flips the progress default off.
    off = build_dataclass(
        SharedBenchParams, _shared_parser().parse_args(["--no-progress"])
    )
    assert off.progress is False


def _shared_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    add_dataclass_args(p, SharedBenchParams)
    return p
