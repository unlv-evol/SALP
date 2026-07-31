"""Standalone-artifact evidence, and its target-repository placement.

A standalone artifact is a non-function file the reusable change needs -- a
configuration file, build script, resource, or test fixture. For a *mapped*
change these are supplementary: the change lands in a function, and any
artifacts alongside it are extra context. For a *standalone-artifact* change
there is no Transformation Unit at all, and the pair
``(artifact identity, verified target placement)`` becomes foundational.

That difference is why placement is a category of its own rather than a field on
the artifact: it applies only to a standalone change, and for a mapped one it is
recorded NOT_APPLICABLE so it leaves both characterization denominators.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from salp.analyzers.base import AnalysisContext, Analyzer, register
from salp.models import Category, CategoryEvidence, ChangeType

# Extensions and path fragments that mark a file as a non-function artifact.
_ARTIFACT_SUFFIXES = frozenset({
    ".xml", ".gradle", ".kts", ".properties", ".yml", ".yaml", ".toml", ".json",
    ".cfg", ".ini", ".md", ".txt", ".sh", ".bat", ".sql", ".proto", ".csv",
})
_ARTIFACT_NAMES = frozenset({
    "Dockerfile", "Makefile", "LICENSE", "NOTICE", ".gitignore", ".editorconfig",
})
_RESOURCE_MARKERS = ("/resources/", "/config/", "/.github/", "/scripts/")


def is_standalone_artifact(path: str) -> bool:
    """Whether a path is a non-function artifact rather than a source file."""
    if not path:
        return False
    p = PurePosixPath(path)
    return (
        p.suffix.lower() in _ARTIFACT_SUFFIXES
        or p.name in _ARTIFACT_NAMES
        or any(marker in f"/{path}" for marker in _RESOURCE_MARKERS)
    )


@register
class StandaloneArtifactAnalyzer(Analyzer):
    """Identifies the non-function artifacts a reusable change carries with it.

    Candidates come from the files the pull request touched alongside the mapped
    change: GACPD retains them as NA/ED siblings, and the ones that are not
    source files are exactly the standalone artifacts.
    """

    category = Category.STANDALONE
    component_name = "standalone-artifact"
    tool = "gacpd"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        raw: Any = ctx.extras.get("context_files") or []
        siblings = [str(s) for s in raw]
        artifacts = [s for s in siblings if is_standalone_artifact(s)]

        if not siblings:
            return self.verified_absent(
                ctx, "the pull request touched no file besides the mapped source"
            )
        if not artifacts:
            # The siblings were all source files, so this change carries no
            # standalone artifact. A completed search, not a gap.
            return self.verified_absent(
                ctx,
                f"none of the {len(siblings)} sibling file(s) is a non-function artifact",
            )

        d = self.draft(ctx, "not recoverable from GACPD output alone")
        d.present("source_artifacts", {"artifacts": artifacts})
        d.present(
            "artifact_locations",
            {
                "locations": artifacts,
                "types": sorted({PurePosixPath(a).suffix.lstrip(".") or "none" for a in artifacts}),
            },
        )
        d.present(
            "artifact_change_relation",
            {"relationships": [
                {"from": f"{ctx.hunk_id}:ER-1", "rel": "accompanied_by", "to": a}
                for a in artifacts
            ]},
        )
        # Whether the target already has a counterpart, and how it differs, needs
        # the target tree; GACPD retains the source side only.
        d.unavailable(
            "target_artifacts", "target-side artifact lookup not yet implemented"
        )
        d.unavailable("artifact_differences", "requires the target-side counterpart")
        d.present(
            "artifact_provenance",
            {"analysis_component": self.component_name, "input_artifacts": ctx.input_artifacts},
        )
        return d.build()


@register
class ArtifactPlacementAnalyzer(Analyzer):
    """Verifies where a standalone artifact belongs in the target repository.

    Foundational for a standalone-artifact change, and NOT_APPLICABLE for a
    mapped one -- a mapped change lands in an aligned function, so it has no
    placement question to answer.
    """

    category = Category.ARTIFACT_PLACEMENT
    component_name = "artifact-placement"
    tool = "git"

    def tool_version(self) -> str | None:
        from salp.analyzers.tools import git_version

        return git_version()

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        if ctx.change_type is not ChangeType.STANDALONE:
            return self.not_applicable(
                ctx, "a mapped change lands in an aligned function, not at a new path"
            )

        pin = ctx.target_pin
        d = self.draft(ctx, "target repository not available; run `salp fetch-repos`")
        d.present(
            "target_repo_revision",
            {"repo": ctx.target_repo, "revision": pin.commit if pin else None},
            pin=pin,
        )
        if ctx.target_path:
            d.present("target_location", {"target_path": ctx.target_path})
        d.present(
            "placement_provenance",
            {"analysis_component": self.component_name, "input_artifacts": ctx.input_artifacts},
        )
        return d.build()
