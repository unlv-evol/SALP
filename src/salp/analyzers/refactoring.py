"""Structural evolution, via RefactoringMiner.

Adapted from ``Refactoring_Detection.py`` and
``Refactoring_History_Construction.py`` in
unlv-evol/GACPD_Hunk_Context_Extraction; see
``docs/adoption/gacpd-hunk-context-extraction.md``.

A repository-wide run reports thousands of refactorings, of which only those
touching this SAP's file matter. Three things are taken from the reference
implementation: filtering by file against both location arrays, correlating the
before and after of a code element by position, and retaining the commit that
produced each finding so it stays traceable to a revision.

Positional correlation is applied *conditionally*. On a real run over
linkedin/kafka, 162 of 326 refactorings had left and right arrays of different
lengths -- an Extract Method reported 18 left and 22 right locations, which are
simply all the locations on each side, not pairs. Correlating those by index
would assert a mapping the tool never claimed, so it is done only when the arrays
agree in length.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from salp.analyzers.base import AnalysisContext, Analyzer, register
from salp.analyzers.tools import refactoring_miner_version
from salp.models import Category, CategoryEvidence

# A move or rename at file or type level relocates the landing site itself,
# which bears on adaptation differently from an edit inside a function. The
# reference implementation skipped these ("found a case of rename file, not
# handling it though"); for a reusable change they are the most consequential
# refactorings there are.
FILE_LEVEL = frozenset({
    "Rename File", "Move File", "Move And Rename File",
    "Move Class", "Rename Class", "Move And Rename Class", "Extract Class",
})


@dataclass(frozen=True)
class Location:
    """One side of a refactoring, as RefactoringMiner reports it."""

    file_path: str
    code_element: str | None
    start_line: int | None
    end_line: int | None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Location:
        return cls(
            file_path=str(raw.get("filePath", "")),
            code_element=raw.get("codeElement"),
            start_line=raw.get("startLine"),
            end_line=raw.get("endLine"),
        )

    def names(self, file_name: str) -> bool:
        return bool(file_name) and (
            self.file_path == file_name or self.file_path.endswith(f"/{file_name}")
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "file": self.file_path,
            "element": self.code_element,
            "lines": [self.start_line, self.end_line],
        }


def touching(refactoring: dict[str, Any], file_name: str) -> tuple[list[Location], list[Location]]:
    """The before and after locations of a refactoring that name ``file_name``."""
    left = [Location.parse(x) for x in refactoring.get("leftSideLocations") or []]
    right = [Location.parse(x) for x in refactoring.get("rightSideLocations") or []]
    return (
        [x for x in left if x.names(file_name)],
        [x for x in right if x.names(file_name)],
    )


def entity_mapping(refactoring: dict[str, Any], file_name: str) -> list[dict[str, Any]]:
    """Before/after pairs for a refactoring, when the report supports pairing.

    RefactoringMiner emits parallel arrays only for one-to-one refactorings such
    as Rename Method. Where the arrays differ in length the positions are not
    counterparts, so the locations are reported without a mapping and
    ``correlated`` says so -- an unmapped finding is more useful than an invented
    correspondence.
    """
    left = [Location.parse(x) for x in refactoring.get("leftSideLocations") or []]
    right = [Location.parse(x) for x in refactoring.get("rightSideLocations") or []]
    correlated = len(left) == len(right) and bool(left)

    if correlated:
        return [
            {"source": a.as_dict(), "target": b.as_dict(), "correlated": True}
            for a, b in zip(left, right, strict=True)
            if a.names(file_name) or b.names(file_name)
        ]
    return [
        {"source": x.as_dict(), "target": None, "correlated": False}
        for x in left if x.names(file_name)
    ] + [
        {"source": None, "target": x.as_dict(), "correlated": False}
        for x in right if x.names(file_name)
    ]


def relevant_refactorings(
    commits: list[dict[str, Any]], file_name: str
) -> list[dict[str, Any]]:
    """Flatten a RefactoringMiner report to the refactorings touching one file.

    Each result keeps the commit that produced it, so a finding stays traceable
    to a revision rather than floating free of its history.
    """
    found: list[dict[str, Any]] = []
    for commit in commits:
        for refactoring in commit.get("refactorings") or []:
            left, right = touching(refactoring, file_name)
            if not (left or right):
                continue
            kind = refactoring.get("type")
            found.append({
                "type": kind,
                "description": refactoring.get("description"),
                "markup": refactoring.get("markup"),
                "relocates_landing_site": kind in FILE_LEVEL,
                "commit": commit.get("sha1"),
                "commit_url": commit.get("url"),
                "mapping": entity_mapping(refactoring, file_name),
            })
    return found


@register
class RefactoringAnalyzer(Analyzer):
    """Detects the structural evolution between the pinned states.

    Runs over the target repository between the divergence and cutoff commits,
    then keeps only the refactorings touching the file this SAP is about.
    """

    category = Category.REFACTORING
    component_name = "refactoring"
    tool = "RefactoringMiner"

    _jar: Path | None = None

    def tool_version(self) -> str | None:
        return refactoring_miner_version(self._jar)

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        self._jar = ctx.extras.get("refactoringminer_jar")  # type: ignore[assignment]
        result: Any = ctx.extras.get("refactorings")

        if result is None:
            return self.unavailable(
                ctx,
                "RefactoringMiner not configured; set tools.refactoringminer_jar "
                "and run `salp fetch-repos`",
            )
        if isinstance(result, str):  # the run failed; the string is its diagnostic
            return self.unavailable(ctx, result)

        file_name = Path(ctx.target_path or ctx.source_file).name
        relevant = relevant_refactorings(list(result), file_name)
        if not relevant:
            # A completed run that found nothing touching this file. The target
            # may not have drifted here at all, which is itself the finding.
            return self.verified_absent(
                ctx, f"RefactoringMiner completed; no refactoring affects {file_name}"
            )

        d = self.draft(ctx, "not reported by RefactoringMiner")
        d.present("refactorings", {"refactorings": relevant})

        entities = sorted({
            str(side["element"])
            for r in relevant for pair in r["mapping"]
            for side in (pair["source"], pair["target"])
            if side and side.get("element")
        })
        if entities:
            d.present("affected_entities", {"entities": entities})
        else:
            d.absent("affected_entities", "the reported refactorings name no code element")

        d.present("entity_mappings", {"mappings": [
            {"type": r["type"], "commit": r["commit"], **pair}
            for r in relevant for pair in r["mapping"]
        ]})
        d.present("refactoring_change_relation", {"relationships": [
            {
                "from": f"{ctx.hunk_id}:ER-1",
                "rel": "landing_site_relocated_by" if r["relocates_landing_site"]
                       else "landing_site_refactored_by",
                "to": r["type"],
                "commit": r["commit"],
            }
            for r in relevant
        ]})
        d.present(
            "refactoring_provenance",
            {"analysis_component": self.component_name, "analysis_tool": self.tool},
        )
        return d.build()
