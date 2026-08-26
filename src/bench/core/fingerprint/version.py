"""Probe recording which bench produced a report."""

from __future__ import annotations

import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from bench.core.fingerprint.base import Fingerprint, Probe
from bench.core.fingerprint.git import GitProbe


class BenchVersionProbe(Probe):
    """The installed bench version, plus the commit bench itself is checked out
    at when running from a source tree."""

    __slots__ = ("git_probe",)

    def __init__(self) -> None:
        super().__init__()

        git_binary = shutil.which("git")
        if git_binary is not None:
            self.git_probe = GitProbe(
                Path(__file__).parent,
                key="bench commit",
                git_binary=git_binary,
            )
        else:
            self.git_probe = None

    def collect(self) -> Fingerprint | None:
        try:
            v = version("bench")
        except PackageNotFoundError:
            v = "<dev>"

        out = Fingerprint({"bench version": v})

        if self.git_probe is not None:
            git = self.git_probe.collect()
            if git is not None:
                out |= git

        return out
