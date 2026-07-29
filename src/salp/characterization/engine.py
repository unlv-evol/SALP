"""The scoring engine."""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, Field, field_serializer

from salp.characterization.levels import (
    CoverageLevel,
    FidelityLevel,
    ReadinessLevel,
)
from salp.models import (
    DEFAULT_SPECS,
    FOUNDATIONAL_SETS,
    Category,
    CategoryEvidence,
    CategorySpec,
    ChangeType,
    EvidenceState,
)

_COV_FLOOR = 0.75  # foundational Coverage floor


_FID_LOW = 0.50  # foundational Fidelity low cutoff


_FID_MID = 0.75  # foundational Fidelity moderate cutoff


class CategoryScore(BaseModel):
    category: Category
    coverage: float | None = None  # None if category not required-for-coverage / N/A
    fidelity: float | None = None  # None if no PRESENT element (undefined)
    n_elements: int = 0
    present: int = 0
    verified_absent: int = 0
    unavailable: int = 0
    blocking_conflict: bool = False


class CharacterizationProfile(BaseModel):
    """A characterization result, kept explainable.

    The capped ``readiness_final`` is authoritative, but the uncapped
    ``readiness_base`` and the constraints that lowered it are retained
    alongside it so the result stays traceable.
    """

    coverage_score: float
    coverage_level: CoverageLevel
    fidelity_score: float | None
    fidelity_level: FidelityLevel | None
    readiness_base: float
    readiness_preliminary: ReadinessLevel
    readiness_final: ReadinessLevel
    applied_constraints: list[str] = Field(default_factory=list)
    category_scores: dict[str, CategoryScore] = Field(default_factory=dict)
    framework_version: str = "1.1"

    @field_serializer(
        "coverage_level", "fidelity_level", "readiness_preliminary", "readiness_final"
    )
    def _level_name(self, level: IntEnum | None) -> str | None:
        """Levels are ordered internally but must read as names on disk."""
        return level.name if level is not None else None


def _cov(state: EvidenceState) -> float:
    return 0.0 if state is EvidenceState.UNAVAILABLE else 1.0


def _score_category(ce: CategoryEvidence) -> CategoryScore:
    present = [e for e in ce.elements if e.state is EvidenceState.PRESENT]
    va = [e for e in ce.elements if e.state is EvidenceState.VERIFIED_ABSENT]
    un = [e for e in ce.elements if e.state is EvidenceState.UNAVAILABLE]
    n = len(ce.elements)

    coverage = (sum(_cov(e.state) for e in ce.elements) / n) if n else 0.0
    fidelity = (sum(e.representation for e in present) / len(present)) if present else None

    return CategoryScore(
        category=ce.category,
        coverage=coverage,
        fidelity=fidelity,
        n_elements=n,
        present=len(present),
        verified_absent=len(va),
        unavailable=len(un),
        blocking_conflict=any(e.blocking_conflict for e in ce.elements),
    )


class Characterizer:
    """Computes a CharacterizationProfile for one SAP hunk (or aggregated hunk view)."""

    def __init__(self, specs: dict[Category, CategorySpec] | None = None) -> None:
        self.specs = specs or DEFAULT_SPECS

    def characterize(
        self,
        categories: dict[Category, CategoryEvidence],
        change_type: ChangeType = ChangeType.MAPPED,
        *,
        localization_ambiguous: bool = False,
        edit_region_unassociated: bool = False,
    ) -> CharacterizationProfile:
        cat_scores = {c: _score_category(ce) for c, ce in categories.items()}

        coverage = self._coverage(cat_scores, change_type)
        fidelity = self._fidelity(cat_scores)
        base = self._readiness_base(coverage, fidelity)
        prelim = ReadinessLevel.from_score(base)

        final, constraints = self._apply_caps(
            prelim,
            cat_scores,
            change_type,
            localization_ambiguous=localization_ambiguous,
            edit_region_unassociated=edit_region_unassociated,
        )

        return CharacterizationProfile(
            coverage_score=coverage,
            coverage_level=CoverageLevel.from_score(coverage),
            fidelity_score=fidelity,
            fidelity_level=(FidelityLevel.from_score(fidelity) if fidelity is not None else None),
            readiness_base=base,
            readiness_preliminary=prelim,
            readiness_final=final,
            applied_constraints=constraints,
            category_scores={c.value: s for c, s in cat_scores.items()},
        )

    # --- Coverage: weighted mean over *required*, applicable categories ---------
    def _coverage(self, scores: dict[Category, CategoryScore], ct: ChangeType) -> float:
        num = den = 0.0
        for cat, s in scores.items():
            spec = self.specs[cat]
            if not spec.is_required_for_coverage or ct not in spec.applicable_to:
                continue
            num += spec.weight * (s.coverage or 0.0)
            den += spec.weight
        return num / den if den else 0.0

    # --- Fidelity: weighted mean over categories with >= 1 PRESENT element ------
    def _fidelity(self, scores: dict[Category, CategoryScore]) -> float | None:
        num = den = 0.0
        for cat, s in scores.items():
            if s.fidelity is None:  # no PRESENT element -> excluded
                continue
            w = self.specs[cat].weight
            num += w * s.fidelity
            den += w
        return num / den if den else None

    @staticmethod
    def _readiness_base(coverage: float, fidelity: float | None) -> float:
        if fidelity is None or (coverage + fidelity) == 0:
            return 0.0
        return 2 * coverage * fidelity / (coverage + fidelity)

    # --- Foundational readiness conditions (may only lower the level) -----------
    def _apply_caps(
        self,
        prelim: ReadinessLevel,
        scores: dict[Category, CategoryScore],
        ct: ChangeType,
        *,
        localization_ambiguous: bool,
        edit_region_unassociated: bool,
    ) -> tuple[ReadinessLevel, list[str]]:
        cap = ReadinessLevel.HIGH
        notes: list[str] = []

        def lower(to: ReadinessLevel, why: str) -> None:
            nonlocal cap
            if to < cap:
                cap = to
            notes.append(why)

        foundational = FOUNDATIONAL_SETS.get(ct, frozenset())

        # 7: any blocking-conflict finding anywhere.
        if any(s.blocking_conflict for s in scores.values()):
            lower(ReadinessLevel.LOW, "blocking_conflict")

        # 6: edit region cannot be associated with the aligned target context.
        if edit_region_unassociated:
            lower(ReadinessLevel.LOW, "edit_region_unassociated")

        for cat in foundational:
            s = scores.get(cat)
            if s is None:
                lower(ReadinessLevel.LOW, f"foundational_missing:{cat.value}")
                continue
            # 1: any UNAVAILABLE foundational element.
            if s.unavailable > 0:
                lower(ReadinessLevel.LOW, f"foundational_unavailable:{cat.value}")
            # 2: foundational Coverage floor.
            if (s.coverage or 0.0) < _COV_FLOOR:
                lower(ReadinessLevel.LOW, f"foundational_coverage_lt_075:{cat.value}")
            # 3 / 4: foundational Fidelity gates.
            if s.fidelity is not None:
                if s.fidelity < _FID_LOW:
                    lower(ReadinessLevel.LOW, f"foundational_fidelity_lt_050:{cat.value}")
                elif s.fidelity < _FID_MID:
                    lower(ReadinessLevel.MODERATE, f"foundational_fidelity_050_075:{cat.value}")

        # 5: unresolved target-localization ambiguity.
        if localization_ambiguous:
            lower(ReadinessLevel.MODERATE, "localization_ambiguous")

        return ReadinessLevel(min(prelim, cap)), notes


def aggregate_readiness(profiles: list[CharacterizationProfile]) -> ReadinessLevel:
    """Composite SAP Readiness is the minimum over its hunks."""
    if not profiles:
        return ReadinessLevel.LOW
    return ReadinessLevel(min(p.readiness_final for p in profiles))
