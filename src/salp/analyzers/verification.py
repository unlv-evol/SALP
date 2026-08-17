"""Target-side oracle discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from salp.analyzers.base import AnalysisContext, Analyzer, register
from salp.analyzers.tools import git_version
from salp.models import (
    Category,
    CategoryEvidence,
)


@dataclass


@register
class VerificationAnalyzer(Analyzer):
    """Finds the target-side oracle: tests that exercise the target edit region.

    Verification is required *whenever such tests exist*; when the search
    completes and finds none, that is VERIFIED_ABSENT, not a gap. The search runs
    over the target repository at its pinned state, so it reflects the exact
    revision the SAP is bound to.
    """

    category = Category.VERIFICATION
    component_name = "verification"
    tool = "git-grep"

    def tool_version(self) -> str | None:
        return git_version()

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        tests: Any = ctx.extras.get("covering_tests")
        if tests is None:
            return self.unavailable(
                ctx, "target repository not available; run `salp fetch-repos`"
            )

        entity = ctx.extras.get("target_entity") or ctx.fn_id
        if not tests:
            # A completed search that found nothing: the change has no oracle.
            return self.verified_absent(
                ctx, f"no target test references {entity} at the pinned revision"
            )

        # §13 distinguishes full from partial coverage. A test naming the edited
        # *method* exercises the edit region; one naming only the enclosing type
        # covers the class but not necessarily the region -- partial. Establishing
        # true line coverage would mean running the suite, which SALP does not do.
        raw_region: Any = ctx.extras.get("region_tests") or []
        region_tests = [str(t) for t in raw_region]
        scope = "full" if region_tests else "partial"
        verifiability = 1.0 if region_tests else 0.5

        d = self.draft(ctx, "no covering test to describe")
        d.present("covering_tests", {
            "tests": tests,
            "region_tests": region_tests,
            "coverage_scope": scope,
            "verifiability": verifiability,
        })
        d.present(
            "test_entity_mapping",
            {"mappings": [{"test": path, "entity": entity} for path in tests]},
        )
        # Establishing pass/fail means building and running the target suite,
        # which SALP deliberately does not do: the cache holds no working tree.
        d.unavailable(
            "pre_adaptation_status",
            "pass/fail requires executing the target suite, which SALP does not do",
        )
        d.unavailable(
            "behavioral_contract", "no assertions recovered from the covering tests"
        )
        return d.build()
