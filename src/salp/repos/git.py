"""A thin, explicit wrapper around the git command line."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from salp.config import get_logger

log = get_logger(__name__)

__all__ =[
    "is_merge_commit"
]

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

def is_merge_commit(commit_sha: str, repo_path: str = ".") -> bool:
    """
    Returns True if commit_sha is a merge commit (has > 1 parent), False otherwise. If commit
    sha is a false value, this function returns False.
    """
    cmd = ["git", "log", "-1", "--format=%P", commit_sha]
    try:
        result = subprocess.run(
            cmd, 
            cwd=repo_path, 
            capture_output=True, 
            text=True, 
            check=True
        )
    except subprocess.CalledProcessError:
        print('yeah error happened')
        return False 
    parents = result.stdout.strip().split()
    return len(parents) > 1

def is_commit_present(commit_sha:str = "", repo_dir:Path = None) -> bool:
    if commit_sha == "":
        return False
    if not repo_dir:
        return False
    
    command = [
        'git',
        'cat-file',
        '-e',
        commit_sha
    ]
    result = subprocess.run(command, cwd = str(repo_dir), text= True)
    code = result.returncode
    if code == 0:
        return True
    else:
        return False
    