"""Runners: consume the planned benchmarks and produce a `Report`."""

from bench.runner.base import Runner
from bench.runner.controller import Controller
from bench.runner.dry import DryRunner
from bench.runner.parallel import Parallel
from bench.runner.sequential import SequentialRunner

__all__ = [
    "Runner",
    "Controller",
    "DryRunner",
    "Parallel",
    "SequentialRunner",
]
