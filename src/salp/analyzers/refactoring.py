"""Structural evolution, via RefactoringMiner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from salp.analyzers.base import AnalysisContext, Analyzer, register
from salp.models import (
    Category,
    CategoryEvidence,
)


@dataclass


@register
class RefactoringAnalyzer(Analyzer):
    """Detects the structural evolution between the pinned states.

    Runs RefactoringMiner over the target repository between the divergence and
    cutoff commits, then keeps only the refactorings touching the file this SAP
    is about -- a repository-wide run reports thousands, and the ones relevant to
    a reusable change are those affecting its landing site.
    """

    category = Category.REFACTORING
    component_name = "refactoring"
    tool = "RefactoringMiner"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        result: Any = ctx.extras.get("refactorings")
        if result is None:
            return self.unavailable(
                ctx,
                "RefactoringMiner not configured; set tools.refactoringminer_jar "
                "and run `salp fetch-repos`",
            )
        if isinstance(result, str):  # the run failed; the string is its diagnostic
            return self.unavailable(ctx, result)

        relevant = [r for r in result if _touches(r, ctx.target_path or ctx.source_file)]
        if not relevant:
            # A completed run that found nothing affecting this file.
            return self.verified_absent(
                ctx, "RefactoringMiner completed; no refactoring affects this file"
            )

        d = self.draft(ctx, "not reported by RefactoringMiner")
        d.present("refactorings", {"refactorings": [
            {"type": r.get("type"), "description": r.get("description")} for r in relevant
        ]})
        entities = sorted({
            side.get("codeElement", "")
            for r in relevant
            for key in ("leftSideLocations", "rightSideLocations")
            for side in r.get(key, [])
            if side.get("codeElement")
        })
        if entities:
            d.present("affected_entities", {"entities": entities})
        else:
            d.absent("affected_entities", "the reported refactorings name no code element")

        mappings = [
            {"type": r.get("type"),
             "source": _first_element(r, "leftSideLocations"),
             "target": _first_element(r, "rightSideLocations")}
            for r in relevant
        ]
        d.present("entity_mappings", {"mappings": mappings})
        d.present("refactoring_change_relation", {"relationships": [
            {"from": f"{ctx.hunk_id}:ER-1", "rel": "landing_site_refactored_by",
             "to": r.get("type")}
            for r in relevant
        ]})
        d.present(
            "refactoring_provenance",
            {"analysis_component": self.component_name, "analysis_tool": self.tool},
        )
        return d.build()


def _touches(refactoring: dict[str, Any], path: str) -> bool:
    """Whether a reported refactoring involves the file this SAP is about."""
    name = path.rsplit("/", 1)[-1]
    for key in ("leftSideLocations", "rightSideLocations"):
        for side in refactoring.get(key, []) or []:
            if name and name in str(side.get("filePath", "")):
                return True
    return False


def _first_element(refactoring: dict[str, Any], key: str) -> str | None:
    for side in refactoring.get(key, []) or []:
        if side.get("codeElement"):
            return str(side["codeElement"])
    return None
