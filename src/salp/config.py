"""Runtime configuration and logging setup.

Loaded from YAML (see configs/default.yaml). Kept intentionally small; the
characterization weights/thresholds live with the model defaults so that a config
change is explicit and versioned.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel


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
    """

    refactoringminer_jar: Path | None = None
    tree_sitter_lib: Path | None = None
    # RefactoringMiner cost tracks repository size as much as commit count; a
    # slow repository is bounded rather than allowed to hang a run.
    refactoringminer_timeout: int = 900


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
