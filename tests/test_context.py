"""Dataclass → argparse glue, and the Context value object."""

import argparse
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from benchr.core.execution import default_success
from benchr.core.metric import Time
from benchr.core.policy import FixedRuns
from benchr.grammar.context import Context, Matrix, add_dataclass_args, build_dataclass


@dataclass
class _Params:
    name: Path                      # required (no default)
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
    ns = _parser().parse_args([
        "--name", "/x", "--iterations", "30", "--cwd", "/y",
        "--verbose", "--label", "tag",
    ])
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
        params=None, suite="S", benchmark="b",
        runs=FixedRuns(3), warmup=FixedRuns(1), timeout=None,
        metrics=(Time(),), harness=False, success=default_success, matrix=Matrix(),
    )
    base.update(overrides)
    return Context(**base)


def test_context_fields():
    ctx = _ctx(params=_Params(name=Path("/x")), matrix=Matrix({"vm": "v8", "size": 100}))
    assert ctx.params.name == Path("/x")
    assert ctx.suite == "S"
    assert ctx.benchmark == "b"
    assert ctx.runs.max_runs() == 3
    assert ctx.warmup.max_runs() == 1
    # Attribute access, consistent with ctx.params.x and ctx.benchmark.
    assert ctx.matrix.vm == "v8"
    assert ctx.matrix.size == 100


def test_context_matrix_missing_key_raises_attribute_error():
    ctx = _ctx(matrix=Matrix({"vm": "v8"}))
    with pytest.raises(AttributeError):
        _ = ctx.matrix.nope


def test_context_suite_level():
    ctx = _ctx(benchmark=None, matrix=Matrix())
    assert ctx.benchmark is None
    with pytest.raises(AttributeError):
        _ = ctx.matrix.vm


def test_context_is_frozen():
    ctx = _ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.suite = "X"  # type: ignore[misc]
