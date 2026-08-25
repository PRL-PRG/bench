"""Probe recording which bench produced a report."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from bench.core.fingerprint.base import Fingerprint, Probe
from bench.core.fingerprint.git import GitProbe, find_git


class BenchVersionProbe(Probe):
    """The installed bench version, plus the commit bench itself is checked out
    at when running from a source tree."""

    __slots__ = ()

    def collect(self) -> Fingerprint | None:
        try:
            v = version("bench")
        except PackageNotFoundError:
            v = "<dev>"

        out: dict[str, Any] = {"bench version": v}

        git_binary = find_git()
        if git_binary is not None:
            probe = GitProbe(
                Path(__file__).parent,
                key="bench commit",
                git_binary=git_binary,
            )
            if probe.is_repository() and (commit := probe.collect()) is not None:
                out |= commit

        return out
