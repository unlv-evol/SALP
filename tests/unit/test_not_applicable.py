"""The NOT_APPLICABLE evidence state and the standalone change-type profile.

NOT_APPLICABLE is removed from *both* the Coverage and the Fidelity denominator,
which is what distinguishes it from UNAVAILABLE (scored zero in Coverage) and
from VERIFIED_ABSENT (credited to Coverage). It exists so that a change type
which genuinely lacks a category is not punished for lacking it — and so that a
well-formed standalone-artifact SAP can reach High Readiness.
"""

from __future__ import annotations

import pytest

from salp.analyzers import AnalysisContext, ArtifactPlacementAnalyzer, is_standalone_artifact
from salp.characterization import Characterizer, ReadinessLevel
from salp.models import (
    FOUNDATIONAL_SETS,
    REQUIRED_SETS,
    Category,
    CategoryEvidence,
    ChangeType,
    EvidenceObject,
    EvidenceState,
)


def _obj(state: EvidenceState, rep: float = 1.0) -> EvidenceObject:
    return EvidenceObject(object_id=f"o-{state}-{rep}", object_type="t", state=state,
                          representation=rep)


def _cat(cat: Category, *states: EvidenceState) -> CategoryEvidence:
    return CategoryEvidence(
        category=cat,
        elements=[
            EvidenceObject(object_id=f"{cat.value}-{i}", object_type=f"{cat.value}.e{i}",
                           state=s)
            for i, s in enumerate(states)
        ],
    )


def _all(cat: Category, state: EvidenceState, n: int = 3) -> CategoryEvidence:
    return _cat(cat, *[state] * n)


# --- the state itself ---------------------------------------------------------
def test_not_applicable_is_a_distinct_state():
    assert EvidenceState.NOT_APPLICABLE.value == "NOT_APPLICABLE"
    assert len(list(EvidenceState)) == 4


def test_not_applicable_leaves_the_coverage_denominator():
    """Contrast with UNAVAILABLE, which is scored zero and drags Coverage down."""
    base = {
        Category.SOURCE_CHANGE: _all(Category.SOURCE_CHANGE, EvidenceState.PRESENT),
        Category.TARGET_LOCALIZATION: _all(Category.TARGET_LOCALIZATION, EvidenceState.PRESENT),
        Category.FUNCTION_TRANSFORMATION: _all(
            Category.FUNCTION_TRANSFORMATION, EvidenceState.PRESENT
        ),
        Category.STRUCTURAL: _all(Category.STRUCTURAL, EvidenceState.PRESENT),
        Category.REFACTORING: _all(Category.REFACTORING, EvidenceState.VERIFIED_ABSENT),
        Category.COMPATIBILITY: _all(Category.COMPATIBILITY, EvidenceState.PRESENT),
        Category.VERIFICATION: _all(Category.VERIFICATION, EvidenceState.PRESENT),
    }
    full = Characterizer().characterize(dict(base), ChangeType.MAPPED)
    assert full.coverage_score == 1.0

    unavailable = dict(base)
    unavailable[Category.VERIFICATION] = _all(Category.VERIFICATION, EvidenceState.UNAVAILABLE)
    assert Characterizer().characterize(unavailable, ChangeType.MAPPED).coverage_score < 1.0

    not_applicable = dict(base)
    not_applicable[Category.VERIFICATION] = _all(
        Category.VERIFICATION, EvidenceState.NOT_APPLICABLE
    )
    # removed from the denominator, so the remaining categories still score 1.0
    assert Characterizer().characterize(not_applicable, ChangeType.MAPPED).coverage_score == 1.0


def test_a_partly_not_applicable_category_scores_over_the_rest():
    """m_i counts only applicable elements."""
    mixed = _cat(
        Category.STRUCTURAL,
        EvidenceState.PRESENT,
        EvidenceState.UNAVAILABLE,
        EvidenceState.NOT_APPLICABLE,
        EvidenceState.NOT_APPLICABLE,
    )
    score = Characterizer().characterize({Category.STRUCTURAL: mixed}).category_scores["structural"]
    assert score.not_applicable == 2
    assert score.coverage == 0.5  # 1 of the 2 applicable elements is unavailable


def test_a_wholly_not_applicable_category_has_no_coverage():
    score = Characterizer().characterize(
        {Category.REFACTORING: _all(Category.REFACTORING, EvidenceState.NOT_APPLICABLE)}
    ).category_scores["refactoring"]
    assert score.coverage is None
    assert score.is_applicable is False


def test_not_applicable_leaves_the_fidelity_denominator():
    cats = {
        Category.SOURCE_CHANGE: _all(Category.SOURCE_CHANGE, EvidenceState.PRESENT),
        Category.REFACTORING: _all(Category.REFACTORING, EvidenceState.NOT_APPLICABLE),
    }
    profile = Characterizer().characterize(cats, ChangeType.MAPPED)
    assert profile.category_scores["refactoring"].fidelity is None
    assert profile.fidelity_score == 1.0


def test_a_not_applicable_foundational_category_cannot_cap_readiness():
    """The change-type profile already excluded it; it must not also constrain."""
    cats = {
        Category.SOURCE_CHANGE: _all(Category.SOURCE_CHANGE, EvidenceState.PRESENT),
        Category.TARGET_LOCALIZATION: _all(
            Category.TARGET_LOCALIZATION, EvidenceState.NOT_APPLICABLE
        ),
        Category.FUNCTION_TRANSFORMATION: _all(
            Category.FUNCTION_TRANSFORMATION, EvidenceState.NOT_APPLICABLE
        ),
    }
    profile = Characterizer().characterize(cats, ChangeType.MAPPED)
    assert profile.applied_constraints == []


# --- the standalone change-type profile --------------------------------------
def test_the_standalone_foundational_set_is_artifact_source_and_placement():
    assert FOUNDATIONAL_SETS[ChangeType.STANDALONE] == frozenset(
        {Category.STANDALONE, Category.ARTIFACT_PLACEMENT}
    )
    required = REQUIRED_SETS[ChangeType.STANDALONE]
    # a standalone artifact has no Transformation Unit
    assert Category.FUNCTION_TRANSFORMATION not in required
    assert Category.TARGET_LOCALIZATION not in required


def test_a_well_formed_standalone_sap_can_reach_high_readiness():
    """The guarantee NOT_APPLICABLE exists to protect.

    Before the state existed, a standalone SAP was capped at Low by the
    function-transformation and target-localization categories it structurally
    cannot have.
    """
    cats = {
        Category.STANDALONE: _all(Category.STANDALONE, EvidenceState.PRESENT),
        Category.ARTIFACT_PLACEMENT: _all(Category.ARTIFACT_PLACEMENT, EvidenceState.PRESENT),
        Category.SOURCE_CHANGE: _all(Category.SOURCE_CHANGE, EvidenceState.PRESENT),
        Category.STRUCTURAL: _all(Category.STRUCTURAL, EvidenceState.PRESENT),
        Category.COMPATIBILITY: _all(Category.COMPATIBILITY, EvidenceState.VERIFIED_ABSENT),
        Category.VERIFICATION: _all(Category.VERIFICATION, EvidenceState.VERIFIED_ABSENT),
        # the two the change type does not have
        Category.FUNCTION_TRANSFORMATION: _all(
            Category.FUNCTION_TRANSFORMATION, EvidenceState.NOT_APPLICABLE
        ),
        Category.TARGET_LOCALIZATION: _all(
            Category.TARGET_LOCALIZATION, EvidenceState.NOT_APPLICABLE
        ),
    }
    profile = Characterizer().characterize(cats, ChangeType.STANDALONE)
    assert profile.coverage_score == 1.0
    assert profile.readiness_final is ReadinessLevel.HIGH
    assert profile.applied_constraints == []


# --- the analyzers -----------------------------------------------------------
@pytest.mark.parametrize("path,expected", [
    ("core/src/main/resources/log4j.properties", True),
    ("build.gradle", True),
    ("pom.xml", True),
    (".github/workflows/ci.yml", True),
    ("Dockerfile", True),
    ("docs/README.md", True),
    ("core/src/main/java/org/example/Wallet.java", False),
    ("core/src/main/scala/Server.scala", False),
])
def test_artifact_classification(path: str, expected: bool):
    assert is_standalone_artifact(path) is expected


def test_placement_is_not_applicable_to_a_mapped_change():
    ctx = AnalysisContext(
        hunk_id="H-1", fn_id="X", source_file="X.java", change_type=ChangeType.MAPPED
    )
    ce = ArtifactPlacementAnalyzer().investigate(ctx)
    assert all(e.state is EvidenceState.NOT_APPLICABLE for e in ce.elements)
    assert "mapped change lands in an aligned function" in ce.elements[0].provenance.diagnostics


def test_placement_investigates_a_standalone_change():
    ctx = AnalysisContext(
        hunk_id="H-1", fn_id="X", source_file="log4j.properties",
        change_type=ChangeType.STANDALONE, target_repo="acme/target",
        target_path="core/src/main/resources/log4j.properties",
    )
    ce = ArtifactPlacementAnalyzer().investigate(ctx)
    assert not any(e.state is EvidenceState.NOT_APPLICABLE for e in ce.elements)
    location = next(e for e in ce.elements if e.object_type.endswith("target_location"))
    assert location.state is EvidenceState.PRESENT
