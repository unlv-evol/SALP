"""Tests for the required-element catalog and the partial-representation rule.

Characterization is computed over each category's required information
elements, so these lock in that a category carries an outcome for *every*
element it declares -- an element that vanished from the catalog would silently
inflate Coverage by shrinking its denominator.
"""

from __future__ import annotations

import pytest

from salp.analyzers import AnalysisContext, Analyzer, build_all
from salp.models import CATEGORY_ELEMENTS, Category, CategoryEvidence, EvidenceState, elements_for


@pytest.fixture
def ctx() -> AnalysisContext:
    return AnalysisContext(
        hunk_id="H-1",
        fn_id="CombinedKey",
        source_file="CombinedKey.java",
        ext="java",
        source_before="void a(){}",
        source_after="void a(){/*x*/}",
        diff="@@ -1 +1 @@",
        target_path="linkedin/kafka/CombinedKey.java",
        source_repo="apache/kafka",
        target_repo="linkedin/kafka",
    )


def test_every_category_declares_required_elements():
    for category in Category:
        assert elements_for(category), f"{category.value} declares no required elements"


def test_element_ids_are_unique_within_a_category():
    for category, specs in CATEGORY_ELEMENTS.items():
        ids = [s.element_id for s in specs]
        assert len(ids) == len(set(ids)), f"{category.value} repeats an element id"


# --- the partial-representation rule -----------------------------------------
def test_full_representation_scores_one():
    spec = elements_for(Category.SOURCE_CHANGE)[0]  # fields: repo, revision
    assert spec.representation({"repo": "apache/kafka", "revision": "abc123"}) == 1.0


def test_partial_representation_is_the_fraction_of_recovered_fields():
    spec = elements_for(Category.SOURCE_CHANGE)[0]
    # GACPD emits repository names but not commit SHAs: half the element
    assert spec.representation({"repo": "apache/kafka", "revision": None}) == 0.5


def test_empty_values_do_not_count_as_represented():
    spec = elements_for(Category.SOURCE_CHANGE)[0]
    assert spec.representation({"repo": "", "revision": []}) == 0.0


def test_element_with_no_declared_fields_is_fully_represented():
    spec = elements_for(Category.FUNCTION_TRANSFORMATION)[1]  # payload_ref only
    assert spec.representation({"payload_ref": "transformation.json"}) == 1.0


# --- the analyzer contract ----------------------------------------------------
class _Probe(Analyzer):
    category = Category.REFACTORING
    component_name = "probe"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:  # pragma: no cover
        return self.unavailable(ctx, "probe")


def test_an_element_with_nothing_recovered_is_unavailable_not_present_at_zero(ctx):
    """rep(e) must stay in (0, 1]; an element with no recovered field is UNAVAILABLE."""
    probe = _Probe()
    spec = elements_for(Category.REFACTORING)[0]
    obj = probe.element(ctx, spec, {"refactorings": None})
    assert obj.state is EvidenceState.UNAVAILABLE
    assert obj.representation == 1.0  # unused for non-PRESENT states


def test_draft_seeds_every_element_and_records_overrides(ctx):
    probe = _Probe()
    draft = probe.draft(ctx, "not investigated")
    draft.present("refactorings", {"refactorings": ["Rename Method"]})
    draft.absent("entity_mappings", "no mappings induced")
    ce = draft.build()

    assert len(ce.elements) == len(elements_for(Category.REFACTORING))
    by_id = {e.object_type.split(".", 1)[1]: e for e in ce.elements}
    assert by_id["refactorings"].state is EvidenceState.PRESENT
    assert by_id["entity_mappings"].state is EvidenceState.VERIFIED_ABSENT
    # an element the analyzer never touched stays an explicit UNAVAILABLE
    assert by_id["affected_entities"].state is EvidenceState.UNAVAILABLE
    assert by_id["affected_entities"].provenance.diagnostics == "not investigated"


def test_unknown_element_id_is_rejected(ctx):
    with pytest.raises(KeyError):
        _Probe().draft(ctx, "seed").present("no_such_element", {})


# --- built-in analyzers -------------------------------------------------------
def test_every_builtin_analyzer_covers_its_whole_catalog(ctx):
    for analyzer in build_all():
        ce = analyzer.investigate(ctx)
        expected = {s.element_id for s in elements_for(analyzer.category)}
        recorded = {e.object_type.split(".", 1)[1] for e in ce.elements}
        assert recorded == expected, f"{analyzer.component_name} skipped elements"


def test_single_hunk_change_verifies_absence_of_an_ordering_relation(ctx):
    """A single-hunk change is atomic: no ordering exists, and that is a finding."""
    from salp.analyzers import TransformationAnalyzer

    ce = TransformationAnalyzer().investigate(ctx)
    ordering = next(e for e in ce.elements if e.object_type.endswith("transformation_ordering"))
    assert ordering.state is EvidenceState.VERIFIED_ABSENT


def test_partial_pin_lowers_fidelity_without_failing_the_investigation(ctx):
    """GACPD emits dates, not SHAs, so the revision element is partially represented."""
    from salp.analyzers import SourceChangeAnalyzer

    ce = SourceChangeAnalyzer().investigate(ctx)
    revision = next(e for e in ce.elements if e.object_type.endswith("source_repo_revision"))
    assert revision.state is EvidenceState.PRESENT
    assert revision.representation == 0.5
    assert "revision" in revision.provenance.diagnostics
