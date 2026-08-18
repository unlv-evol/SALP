"""A thin, explicit wrapper around the git command line."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from salp.config import get_logger

log = get_logger(__name__)


# Network operations are slow but bounded; a hung fetch must not wedge a run.
_NETWORK_TIMEOUT = 1800


_LOCAL_TIMEOUT = 120


@dataclass(frozen=True)
class GitResult:
    ok: bool
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        return self.stdout.strip()


def git_available() -> bool:
    return shutil.which("git") is not None


def run(
    *args: str,
    cwd: Path | None = None,
    timeout: int = _LOCAL_TIMEOUT,
) -> GitResult:
    """Run one git command, capturing its outcome rather than raising."""
    if not git_available():
        return GitResult(False, "", "git is not installed or not on PATH")
    try:
        proc = subprocess.run(  # noqa: S603 - fixed executable, arguments are not shell-parsed
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GitResult(False, "", f"git {args[0]} timed out after {timeout}s")
    except OSError as exc:  # pragma: no cover - environment failure
        return GitResult(False, "", f"could not run git: {exc}")

    if proc.returncode != 0:
        log.debug("git %s failed: %s", " ".join(args), proc.stderr.strip())
    return GitResult(proc.returncode == 0, proc.stdout, proc.stderr)


def run_network(*args: str, cwd: Path | None = None) -> GitResult:
    """Run a git command that talks to a remote."""
    return run(*args, cwd=cwd, timeout=_NETWORK_TIMEOUT)


# --- commit-level predicates ---------------------------------------------------
def is_commit_present(commit_sha: str, repo_dir: Path) -> bool:
    """Whether a commit object exists in a repository.

    ``^{commit}`` rather than a bare object check: a hexadecimal string can name
    a blob or a tree, and neither is something a commit-level analysis can use.

    Callers rely on this to keep analysis local. See the note in
    ``analyzers/tools.py`` on RefactoringMiner's ``-c`` command, which falls back
    to the network for a commit it cannot find.
    """
    if not commit_sha:
        return False
    return run("cat-file", "-e", f"{commit_sha}^{{commit}}", cwd=repo_dir).ok


def is_merge_commit(commit_sha: str, repo_dir: Path) -> bool:
    """Whether a commit has more than one parent.

    A merge's diff attributes every reconciled change to the merge itself, so
    counting it would credit a landing site with refactorings no one performed.

    Returns False when the commit cannot be read at all -- absent, or not a
    commit. That is deliberately indistinguishable from "not a merge" because
    the answer is only ever used to *skip* work; use ``is_commit_present`` first
    where the difference matters.
    """
    if not commit_sha:
        return False
    result = run("log", "-1", "--format=%P", commit_sha, cwd=repo_dir)
    if not result.ok:
        return False
    return len(result.text.split()) > 1
