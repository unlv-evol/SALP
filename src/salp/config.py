"""Runtime configuration and logging setup.

Loaded from YAML (see configs/default.yaml). Kept intentionally small; the
characterization weights/thresholds live with the model defaults so that a config
change is explicit and versioned.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class Paths(BaseModel):
    gacpd_run: Path = Path("./data/gacpd")
    output: Path = Path("./data/out")
    # Bare clones of the source and target repositories, populated by
    # `salp fetch-repos`. A run reads them locally and never fetches.
    repo_cache: Path = Path("./data/repos")


class Tools(BaseModel):
    """External analysis tools, installed under ``tools/``.

    ``None`` means "not configured": the corresponding analyzer records
    UNAVAILABLE with a diagnostic rather than failing the build, since an unrun
    investigation is an information gap and not an error.

    Unknown keys are rejected. A misspelled or renamed setting would otherwise be
    accepted and ignored, and the run would report a whole evidence category as
    UNAVAILABLE with no indication that the config was the cause.
    """

    model_config = ConfigDict(extra="forbid")

    refactoringminer_jar: Path | None = None
    tree_sitter_lib: Path | None = None
    # RefactoringMiner cost tracks repository size as much as commit count; a
    # slow repository is bounded rather than allowed to hang a run.
    refactoringminer_timeout: int = 900

    def refactoringminer_launcher(self) -> Path | None:
        """The RefactoringMiner launcher for the platform this run is on.

        The distribution ships two launchers side by side -- ``bin/Refactoring‌Miner``
        and ``bin/RefactoringMiner.bat`` -- and only the second works on Windows.
        The configured path names the Unix one; the Windows variant is derived
        from it rather than configured separately, so one config file stays
        correct on every machine that shares it.

        Falls back to the configured path when no ``.bat`` sits beside it, which
        keeps the failure where it belongs: the analyzer reports what it could
        not run, rather than this silently reporting "not configured".
        """
        if self.refactoringminer_jar is None or os.name != "nt":
            return self.refactoringminer_jar
        windows = self.refactoringminer_jar.with_suffix(".bat")
        return windows if windows.is_file() else self.refactoringminer_jar


class Config(BaseModel):
    paths: Paths = Paths()
    tools: Tools = Tools()
    framework_version: str = "1.1"
    log_level: str = "INFO"
    # Resolve repository-state pins against the local clone cache. Disabling
    # leaves every pin date-based, which lowers Fidelity but never fails a run.
    resolve_pins: bool = True
    # Refactoring detection is the most expensive analysis; disabling it leaves
    # the category UNAVAILABLE with a diagnostic rather than skipping it.
    detect_refactorings: bool = True

    @classmethod
    def load(cls, path: str | Path | None) -> Config:
        if path is None:
            return cls()
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)


# --- logging ------------------------------------------------------------------


def configure(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
