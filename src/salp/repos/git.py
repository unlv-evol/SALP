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
