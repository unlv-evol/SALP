"""Tests for the corrected characterization engine.

These lock in the invariants that motivated the metric redefinition:
Coverage and Fidelity are independent, VERIFIED_ABSENT is credited to Coverage
and excluded from Fidelity, and foundational conditions cap Readiness.
"""

from __future__ import annotations

from salp.characterization import Characterizer, FidelityLevel, ReadinessLevel
from salp.models import Category, CategoryEvidence, ChangeType, EvidenceObject, EvidenceState


def _obj(
    oid: str, state: EvidenceState, rep: float = 1.0, blocking: bool = False
) -> EvidenceObject:
    return EvidenceObject(
        object_id=oid, object_type="t", state=state, representation=rep, blocking_conflict=blocking
    )


def _cat(cat: Category, *objs: EvidenceObject) -> CategoryEvidence:
    return CategoryEvidence(category=cat, elements=list(objs))


def _foundational_present() -> dict[Category, CategoryEvidence]:
    return {
        Category.SOURCE_CHANGE: _cat(
            Category.SOURCE_CHANGE, _obj("s", EvidenceState.PRESENT)),
        Category.TARGET_LOCALIZATION: _cat(
            Category.TARGET_LOCALIZATION, _obj("t", EvidenceState.PRESENT)),
        Category.FUNCTION_TRANSFORMATION: _cat(
            Category.FUNCTION_TRANSFORMATION, _obj("f", EvidenceState.PRESENT)),
    }


def test_verified_absent_gets_full_coverage_and_is_excluded_from_fidelity():
    cats = _foundational_present()
    # a fully verified-absent semantic category
    cats[Category.REFACTORING] = _cat(
        Category.REFACTORING, _obj("r", EvidenceState.VERIFIED_ABSENT))
    prof = Characterizer().characterize(cats, ChangeType.MAPPED)

    # refactoring counts as resolved for Coverage
    assert prof.category_scores["refactoring"].coverage == 1.0
    # ... but has no Fidelity (undefined -> None), so it does not drag Fidelity down
    assert prof.category_scores["refactoring"].fidelity is None
    assert prof.fidelity_score == 1.0  # only PRESENT foundational elements count


def test_coverage_and_fidelity_are_independent():
    # one PRESENT element + three UNAVAILABLE in the same category:
    # Coverage = 0.25, Fidelity = 1.0  (impossible under the old collinear metric)
    cat = _cat(
        Category.STRUCTURAL,
        _obj("a", EvidenceState.PRESENT, rep=1.0),
        _obj("b", EvidenceState.UNAVAILABLE),
        _obj("c", EvidenceState.UNAVAILABLE),
        _obj("d", EvidenceState.UNAVAILABLE),
    )
    prof = Characterizer().characterize({**_foundational_present(), Category.STRUCTURAL: cat},
                                        ChangeType.MAPPED)
    s = prof.category_scores["structural"]
    assert s.coverage == 0.25
    assert s.fidelity == 1.0


def test_all_present_fully_represented_is_high_readiness():
    prof = Characterizer().characterize(_foundational_present(), ChangeType.MAPPED)
    assert prof.coverage_score == 1.0
    assert prof.fidelity_score == 1.0
    assert prof.readiness_final == ReadinessLevel.HIGH


def test_unavailable_foundational_caps_low():
    cats = _foundational_present()
    cats[Category.TARGET_LOCALIZATION] = _cat(
        Category.TARGET_LOCALIZATION, _obj("t", EvidenceState.UNAVAILABLE)
    )
    prof = Characterizer().characterize(cats, ChangeType.MAPPED)
    assert prof.readiness_final == ReadinessLevel.LOW
    assert any("foundational_unavailable" in c for c in prof.applied_constraints)


def test_partial_representation_lowers_fidelity_only():
    cats = _foundational_present()
    cats[Category.SOURCE_CHANGE] = _cat(
        Category.SOURCE_CHANGE, _obj("s", EvidenceState.PRESENT, rep=0.5)
    )
    prof = Characterizer().characterize(cats, ChangeType.MAPPED)
    assert prof.coverage_score == 1.0  # still fully investigated
    assert prof.fidelity_score is not None and prof.fidelity_score < 1.0


def test_blocking_conflict_caps_low():
    cats = _foundational_present()
    cats[Category.COMPATIBILITY] = _cat(
        Category.COMPATIBILITY, _obj("api", EvidenceState.PRESENT, blocking=True)
    )
    prof = Characterizer().characterize(cats, ChangeType.MAPPED)
    assert prof.readiness_final == ReadinessLevel.LOW
    assert "blocking_conflict" in prof.applied_constraints


def test_localization_ambiguity_caps_moderate():
    prof = Characterizer().characterize(
        _foundational_present(), ChangeType.MAPPED, localization_ambiguous=True
    )
    assert prof.readiness_final <= ReadinessLevel.MODERATE
    assert "localization_ambiguous" in prof.applied_constraints


def test_optional_unavailable_does_not_reduce_coverage():
    cats = _foundational_present()
    cats[Category.SURROUNDING] = _cat(Category.SURROUNDING, _obj("x", EvidenceState.UNAVAILABLE))
    prof = Characterizer().characterize(cats, ChangeType.MAPPED)
    assert prof.coverage_score == 1.0  # optional category excluded from Coverage
    assert prof.fidelity_level == FidelityLevel.VERY_RICH
