"""External analysis tools.

Tools live under ``tools/`` and are configured by path. An unconfigured or
failing tool is an information gap, never an error: every runner here returns
either a result or a diagnostic string, and the analyzer records UNAVAILABLE
with that diagnostic.

Command shapes are adopted from ``Refactoring_Detection.py`` in
unlv-evol/GACPD_Hunk_Context_Extraction; see
``docs/adoption/gacpd-hunk-context-extraction.md``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

from salp.config import get_logger

log = get_logger(__name__)

# RefactoringMiner walks every commit in the range, parsing every file at each,
# so cost tracks repository size as much as commit count -- a modest range over a
# large repository can run for many minutes. The run is bounded so that one slow
# repository cannot hang a pipeline, and cached so it is paid for once.
DEFAULT_TIMEOUT = 900


def _cache_path(cache_dir: Path, repo_dir: Path, start: str, end: str) -> Path:
    return cache_dir / f"{repo_dir.name}__{start[:12]}__{end[:12]}.json"


@lru_cache(maxsize=32)
def run_refactoring_miner(
    jar: Path | None,
    repo_dir: Path,
    start_sha: str | None,
    end_sha: str | None,
    cache_dir: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[dict[str, Any], ...] | str:
    """Refactorings between two commits, or a diagnostic explaining the gap.

    Uses the ``-bc`` (between commits) form: both endpoints are already resolved
    as repository-state pins, so it needs no network and no GitHub token.

    Cached twice over. In-process, because every pull request of a variant pair
    shares one divergence-to-cutoff range; and on disk under ``cache_dir``,
    because the analysis is expensive, deterministic in its inputs, and would
    otherwise be repeated on every run. The result is a tuple because a cached
    value must not be mutated by a caller.
    """
    if jar is None:
        return "tools.refactoringminer_jar is not configured"
    if not Path(jar).exists():
        return f"RefactoringMiner not found at {jar}"
    if not os.access(jar, os.X_OK):
        return f"RefactoringMiner at {jar} is not executable"
    if not (start_sha and end_sha):
        return "both endpoint commits must be resolved to run RefactoringMiner"
    if not shutil.which("java"):
        return "java is not installed or not on PATH"

    cached = _cache_path(cache_dir, repo_dir, start_sha, end_sha) if cache_dir else None
    if cached is not None and cached.is_file():
        try:
            report = json.loads(cached.read_text(encoding="utf-8"))
            log.info("reusing cached refactoring analysis for %s", repo_dir.name)
            return tuple(report.get("commits") or ())
        except (OSError, json.JSONDecodeError):
            log.warning("discarding unreadable refactoring cache %s", cached)

    log.info(
        "analysing %s for refactorings between %s and %s (up to %ds; "
        "the result is cached for later runs)",
        repo_dir.name, start_sha[:10], end_sha[:10], timeout,
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "refactorings.json"
        if start_sha == end_sha:
            # No drift between the pinned states: nothing to analyse, and the
            # tool would report an empty range rather than an absence.
            return ()
        command = [
            str(jar), "-bc", str(Path(repo_dir).resolve()),
            start_sha, end_sha, "-json", str(out),
        ]
        try:
            proc = subprocess.run(  # noqa: S603 - configured path, no shell
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return (
                f"RefactoringMiner exceeded {timeout}s on {repo_dir.name}; raise "
                "tools.refactoringminer_timeout or disable it with --no-refactorings"
            )
        except OSError as exc:
            return f"could not run RefactoringMiner: {exc}"

        if proc.returncode != 0:
            return f"RefactoringMiner exited {proc.returncode}: {proc.stderr.strip()[:200]}"
        if not out.is_file():
            return "RefactoringMiner produced no output file"
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"could not read the RefactoringMiner report: {exc}"

    if cached is not None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(report), encoding="utf-8")
        except OSError as exc:  # a cache that cannot be written is not an error
            log.debug("could not cache the refactoring analysis: %s", exc)

    # the per-commit structure is retained: a finding stays traceable to the
    # revision that produced it
    commits = tuple(report.get("commits") or ())
    log.info(
        "%s: %d commit(s), %d refactoring(s)", repo_dir.name, len(commits),
        sum(len(c.get("refactorings") or ()) for c in commits),
    )
    return commits

@lru_cache(maxsize=32)
def run_refactoring_miner_list(
    jar: Path | None,
    repo_dir: Path,
    sha_list: list[str] | None = None,
    cache_dir: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[dict[str, Any], ...] | str:
    """
    Detects the refactorings for a list of commits. Uses the RefactoringMiner
    "-c" command on each commit.
    """
    if jar is None:
        return "tools.refactoringminer_jar is not configured"
    if not Path(jar).exists():
        return f"RefactoringMiner not found at {jar}"
    if not os.access(jar, os.X_OK):
        return f"RefactoringMiner at {jar} is not executable"
    if not sha_list:
        return "The commit list must have at list 1 commit"
    if not shutil.which("java"):
        return "java is not installed or not on PATH"
    commits = []
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "refactorigns.json"
    
        for commit_sha in sha_list:
            command = [
                str(jar), "-c", str(Path(repo_dir).resolve()),
                commit_sha, "-json", str(out)
            ]
            try:
                proc = subprocess.run(
                    command, capture_output=True, text=True, timeout=timeout, check = False
                )
            except subprocess.TimeoutExpired:
                return (
                    f"RefactoringMiner exceeded {timeout}s on {repo_dir.name}; raise "
                    "tools.refactoringminer_timeout or disable it with --no-refactorings"
                )
            except OSError as exc:
                return f"could not run RefactoringMiner: {exc}"

            if proc.returncode != 0:
                return f"RefactoringMiner exited {proc.returncode}: {proc.stderr.strip()[:200]}"
            if not out.is_file():
                return "RefactoringMiner produced no output file"
            try:
                report = json.loads(out.read_text(encoding="utf-8"))
                # Even the -c command returns an array of commits, though it always 
                # contains only one commits, hence ['commits'][0].
                commits.append(tuple(report['commits'][0] or ()))
            except (OSError, json.JSONDecodeError) as exc:
                return f"could not read the RefactoringMiner report: {exc}"
    log.info(
        "%s: %d commit(s), %d refactoring(s)", repo_dir.name, len(commits),
        sum(len(c.get("refactorings") or ()) for c in commits),
    )
    return commits
  
# --- tool versions, for provenance -------------------------------------------
# Evidence is reproducible only under fixed tool versions, so every analyzer
# backed by an external tool records the version actually in use rather than one
# written into the source.


@lru_cache(maxsize=1)
def tree_sitter_version() -> str | None:
    """The installed tree-sitter and grammar versions, e.g. ``0.26.0+java0.23.5``."""
    try:
        core = metadata.version("tree-sitter")
    except metadata.PackageNotFoundError:
        return None
    try:
        grammar = metadata.version("tree-sitter-java")
    except metadata.PackageNotFoundError:
        return core
    return f"{core}+java{grammar}"


_RM_DIST = re.compile(r"RefactoringMiner-([\d.]+)")


@lru_cache(maxsize=8)
def refactoring_miner_version(executable: Path | None) -> str | None:
    """The RefactoringMiner version in use.

    The official distribution unpacks to ``RefactoringMiner-<version>/bin/…``, so
    the version is read from the distribution directory. A ``VERSION`` file
    beside the executable overrides it, for builds laid out differently.
    """
    if executable is None:
        return None
    path = Path(executable)
    override = path.parent / "VERSION"
    if override.is_file():
        try:
            return override.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
    for part in path.resolve().parts:
        if match := _RM_DIST.fullmatch(part):
            return match.group(1)
    return None


@lru_cache(maxsize=1)
def git_version() -> str | None:
    """The installed git version, e.g. ``2.43.0``."""
    if not shutil.which("git"):
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - fixed executable
            ["git", "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().removeprefix("git version ") or None
