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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from salp.config import get_logger

log = get_logger(__name__)

# RefactoringMiner walks every commit in the range; a wide range on a large
# repository is slow, so the run is bounded rather than left to hang a pipeline.
_TIMEOUT = 1800


def run_refactoring_miner(
    jar: Path | None, repo_dir: Path, start_sha: str | None, end_sha: str | None
) -> list[dict[str, Any]] | str:
    """Refactorings between two commits, or a diagnostic explaining the gap.

    Uses the ``-bc`` (between commits) form: both endpoints are already resolved
    as repository-state pins, so it needs no network and no GitHub token.
    """
    if jar is None:
        return "tools.refactoringminer_jar is not configured"
    if not Path(jar).exists():
        return f"RefactoringMiner not found at {jar}"
    if not (start_sha and end_sha):
        return "both endpoint commits must be resolved to run RefactoringMiner"
    if not shutil.which("java"):
        return "java is not installed or not on PATH"

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "refactorings.json"
        command = [
            str(jar), "-bc", str(repo_dir), start_sha, end_sha, "-json", str(out),
        ]
        try:
            proc = subprocess.run(  # noqa: S603 - configured path, no shell
                command, capture_output=True, text=True, timeout=_TIMEOUT, check=False
            )
        except subprocess.TimeoutExpired:
            return f"RefactoringMiner timed out after {_TIMEOUT}s"
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

    # the report nests refactorings under per-commit entries
    return [r for commit in report.get("commits", []) for r in commit.get("refactorings", [])]
