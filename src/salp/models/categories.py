"""SAP information categories, their weights, and change-type profiles."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from salp.models.evidence import EvidenceObject


class ChangeType(StrEnum):
    MAPPED = "mapped"  # has a Transformation Unit (source, target, transformation)
    STANDALONE = "standalone"  # artifact with no Transformation Unit


class RequirementLevel(StrEnum):
    FOUNDATIONAL = "foundational"  # adaptation cannot proceed without it
    SEMANTIC = "semantic"  # important semantic support
    OPTIONAL = "optional"  # enriches; never penalizes Coverage when absent
    CONDITIONAL = "conditional"  # applies only when the phenomenon exists


class Category(StrEnum):
    SOURCE_CHANGE = "source_change"
    TARGET_LOCALIZATION = "target_localization"
    FUNCTION_TRANSFORMATION = "function_transformation"
    STRUCTURAL = "structural"
    REFACTORING = "refactoring"
    COMPATIBILITY = "compatibility"
    VERIFICATION = "verification"
    SURROUNDING = "surrounding"
    STANDALONE = "standalone"


class CategorySpec(BaseModel):
    """Static configuration for a category (weight, requirement, applicability)."""

    category: Category
    weight: int
    requirement: RequirementLevel
    # Change types for which this category is applicable and characterized.
    applicable_to: frozenset[ChangeType]

    @property
    def is_required_for_coverage(self) -> bool:
        """Only foundational and semantic-support categories enter Coverage.

        Optional and conditional categories never reduce Coverage when UNAVAILABLE.
        """
        return self.requirement in (RequirementLevel.FOUNDATIONAL, RequirementLevel.SEMANTIC)


_ALL = frozenset(ChangeType)


_MAPPED = frozenset({ChangeType.MAPPED})


_STANDALONE = frozenset({ChangeType.STANDALONE})


# Default characterization configuration (specification Table 4 + verification).
DEFAULT_SPECS: dict[Category, CategorySpec] = {
    Category.SOURCE_CHANGE: CategorySpec(
        category=Category.SOURCE_CHANGE, weight=3,
        requirement=RequirementLevel.FOUNDATIONAL, applicable_to=_ALL),
    Category.TARGET_LOCALIZATION: CategorySpec(
        category=Category.TARGET_LOCALIZATION, weight=3,
        requirement=RequirementLevel.FOUNDATIONAL, applicable_to=_MAPPED),
    Category.FUNCTION_TRANSFORMATION: CategorySpec(
        category=Category.FUNCTION_TRANSFORMATION, weight=3,
        requirement=RequirementLevel.FOUNDATIONAL, applicable_to=_MAPPED),
    Category.STRUCTURAL: CategorySpec(
        category=Category.STRUCTURAL, weight=2,
        requirement=RequirementLevel.SEMANTIC, applicable_to=_ALL),
    Category.REFACTORING: CategorySpec(
        category=Category.REFACTORING, weight=2,
        requirement=RequirementLevel.SEMANTIC, applicable_to=_MAPPED),
    Category.COMPATIBILITY: CategorySpec(
        category=Category.COMPATIBILITY, weight=2,
        requirement=RequirementLevel.SEMANTIC, applicable_to=_ALL),
    Category.VERIFICATION: CategorySpec(
        category=Category.VERIFICATION, weight=2,
        requirement=RequirementLevel.SEMANTIC, applicable_to=_ALL),
    Category.SURROUNDING: CategorySpec(
        category=Category.SURROUNDING, weight=1,
        requirement=RequirementLevel.OPTIONAL, applicable_to=_ALL),
    Category.STANDALONE: CategorySpec(
        category=Category.STANDALONE, weight=1,
        requirement=RequirementLevel.CONDITIONAL, applicable_to=_ALL),
}


# Foundational sets keyed on change type (drive the Readiness caps).
FOUNDATIONAL_SETS: dict[ChangeType, frozenset[Category]] = {
    ChangeType.MAPPED: frozenset({
        Category.SOURCE_CHANGE,
        Category.TARGET_LOCALIZATION,
        Category.FUNCTION_TRANSFORMATION,
    }),
    ChangeType.STANDALONE: frozenset({
        Category.SOURCE_CHANGE,  # artifact-source identity
        # target placement is modelled via TARGET_LOCALIZATION reused for artifacts
    }),
}


class CategoryEvidence(BaseModel):
    """The evidence recovered for one category of one hunk.

    ``payloads`` lets an investigation contribute bulky artifacts -- a normalized
    AST, say -- keyed by the SAP-relative path its elements reference. The
    builder merges them into the SAP's payload store, keeping index and payload
    separate: the elements carry only the reference.
    """

    category: Category
    elements: list[EvidenceObject] = Field(default_factory=list)
    payloads: dict[str, str] = Field(default_factory=dict, exclude=True)

    def spec(self, specs: dict[Category, CategorySpec] | None = None) -> CategorySpec:
        return (specs or DEFAULT_SPECS)[self.category]
