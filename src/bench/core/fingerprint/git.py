"""Probe recording which commit of a working tree a benchmark ran against."""

from __future__ import annotations

import shutil
from pathlib import Path

from bench.core.fingerprint.base import Fingerprint, Probe
from bench.core.process import execute
from bench.model.invocation import Invocation


def run_git(git_binary: str, folder: Path, *args: str) -> str | None:
    """Stdout of a git command, or `None` if git refused (not a repository,
    no such folder, ...)."""
    res = execute(
        Invocation(
            command=[git_binary, *args],
            cwd=folder,
            inherit_env=True,
        )
    )
    return res.stdout.strip() if res.returncode == 0 else None


def find_git() -> str | None:
    return shutil.which("git")


class GitProbe(Probe):
    """The HEAD commit of `folder`, suffixed `(dirty)` when the tree has
    uncommitted changes. `None` when `folder` is not a git repository."""

    __slots__ = ("folder", "key", "git_binary")

    def __init__(
        self,
        folder: Path,
        *,
        key: str = "git commit",
        git_binary: str | Path | None = None,
    ) -> None:
        super().__init__()

        self.folder = folder.resolve()
        self.key = key

        if git_binary is None:
            git_binary = find_git()
            if git_binary is None:
                raise ValueError("No `git` binary is available in PATH")

        self.git_binary = str(git_binary)

    def collect(self) -> Fingerprint | None:
        commit = run_git(self.git_binary, self.folder, "rev-parse", "HEAD")
        if commit is None:
            return None

        # --no-optional-locks so probing never writes to someone else's index.
        dirty = run_git(
            self.git_binary,
            self.folder,
            "--no-optional-locks",
            "status",
            "--porcelain",
        )
        if dirty:
            commit += " (dirty)"

        return {self.key: commit}

    def is_repository(self) -> bool:
        return (
            run_git(self.git_binary, self.folder, "rev-parse", "--is-inside-work-tree")
            == "true"
        )
