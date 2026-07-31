"""Required information elements per SAP information category.

The SAP Design Specification lists, for every information category, the
information elements the pipeline is required to produce. Characterization is
computed *over these elements* -- Coverage counts how many were resolved and
Fidelity scores how completely the recovered ones are represented -- so the
catalog below is what makes both metrics graded rather than all-or-nothing.

Partial-representation rule
---------------------------
The specification requires each category to define the information needed for
full representation and the rule used to compute a partial value. This module
adopts one uniform, documented rule for every category:

    each element declares the attribute keys required for full representation;
    rep(e) = |represented fields| / |required fields|.

An element with no declared fields is fully represented whenever it is PRESENT.
An element whose fields are *all* missing is not partially represented -- it was
not recovered at all, and is recorded UNAVAILABLE (see ``represent``).
"""

from __future__ import annotations

from pydantic import BaseModel

from salp.models.categories import Category


class ElementSpec(BaseModel):
    """One required information element of a category.

    ``fields`` are the attribute keys that must be populated for the element to
    count as fully represented; they drive the partial-representation rule.
    """

    element_id: str
    label: str
    fields: tuple[str, ...] = ()

    def represented_fields(self, attributes: dict[str, object]) -> list[str]:
        """The declared fields carrying usable (non-empty) values."""
        return [f for f in self.fields if attributes.get(f) not in (None, "", [], {}, ())]

    def representation(self, attributes: dict[str, object]) -> float:
        """rep(e) for a PRESENT element, per the rule documented above."""
        if not self.fields:
            return 1.0
        return len(self.represented_fields(attributes)) / len(self.fields)


def _e(element_id: str, label: str, *fields: str) -> ElementSpec:
    return ElementSpec(element_id=element_id, label=label, fields=fields)


# --- Source-Side Change Information ------------------------------------------
_SOURCE_CHANGE = (
    _e("source_repo_revision", "source repository and revision identifiers",
       "repo", "revision"),
    _e("pre_change_function", "the pre-change source function", "payload_ref"),
    _e("post_change_function", "the post-change source function", "payload_ref"),
    _e("reusable_source_change", "the reusable source change", "payload_ref"),
    _e("affected_files_entities", "the affected source files and program entities",
       "source_file", "entities"),
    _e("source_provenance", "provenance required to recover and verify the change",
       "analysis_component", "input_artifacts"),
)

# --- Target-Side Localization Information ------------------------------------
_TARGET_LOCALIZATION = (
    _e("target_repo_revision", "target repository and revision identifiers",
       "repo", "revision"),
    _e("localized_target_function", "the localized target function or candidates",
       "payload_ref"),
    _e("target_file_location", "the target file and location", "target_file"),
    _e("source_target_correspondence", "the source-to-target correspondence",
       "source_file", "target_file"),
    _e("alternative_candidates", "alternative candidates when alignments are plausible",
       "candidates"),
    _e("alignment_confidence", "alignment confidence and ambiguity information",
       "confidence"),
    _e("localization_provenance", "localization provenance",
       "analysis_component", "input_artifacts"),
)

# --- Function-Level Transformation Context -----------------------------------
_FUNCTION_TRANSFORMATION = (
    _e("transformation_unit", "the complete Transformation Unit",
       "f_s_before", "f_s_after", "f_t"),
    _e("normalized_transformation", "the normalized source transformation", "payload_ref"),
    _e("edit_regions", "the affected edit regions", "edit_regions"),
    _e("edit_entity_relationships", "relationships between edits and program entities",
       "relationships"),
    _e("transformation_ordering", "ordering required to preserve change semantics",
       "ordering"),
    _e("transformation_provenance", "transformation provenance",
       "analysis_component", "input_artifacts"),
)

# --- Structural Context Requirements -----------------------------------------
_STRUCTURAL = (
    _e("source_structure", "the source structural representation", "payload_ref"),
    _e("target_structure", "the target structural representation", "payload_ref"),
    _e("edit_region_structure", "structural context surrounding the edit regions", "spans"),
    _e("structural_correspondences", "source-to-target structural correspondences",
       "correspondences"),
    _e("structure_transformation_link", "relationships between structures and edit operations",
       "relationships"),
    _e("structural_provenance", "structural-analysis provenance and diagnostics",
       "analysis_component", "analysis_tool"),
)

# --- Refactoring Context Requirements ----------------------------------------
_REFACTORING = (
    _e("refactorings", "relevant refactorings between source and target contexts",
       "refactorings"),
    _e("affected_entities", "the program entities affected by each refactoring", "entities"),
    _e("entity_mappings", "source-to-target entity mappings induced by the refactorings",
       "mappings"),
    _e("refactoring_change_relation", "the relation between each refactoring and the change",
       "relationships"),
    _e("refactoring_provenance", "refactoring-analysis provenance and diagnostics",
       "analysis_component", "analysis_tool"),
)

# --- API and Dependency Compatibility Requirements ---------------------------
_COMPATIBILITY = (
    _e("source_apis", "APIs referenced or introduced by the reusable source change", "apis"),
    _e("target_apis", "corresponding APIs available in the target repository", "apis"),
    _e("api_mappings", "API mappings, substitutions, or incompatibilities", "mappings"),
    _e("source_dependencies", "dependencies required by the reusable source change",
       "dependencies"),
    _e("target_dependencies", "target-side dependency declarations and versions",
       "dependencies"),
    _e("compatibility_findings", "compatibility findings and unresolved constraints",
       "findings"),
    _e("compatibility_provenance", "compatibility-analysis provenance and diagnostics",
       "analysis_component", "analysis_tool"),
)

# --- Verification Evidence (Behavioral and Verification Context) -------------
_VERIFICATION = (
    _e("covering_tests", "target tests or specifications covering the target edit region",
       "tests"),
    _e("pre_adaptation_status", "their pass/fail status prior to adaptation", "status"),
    _e("behavioral_contract", "the behavioral contract the adapted change must preserve",
       "contract"),
    _e("test_entity_mapping", "the mapping between each test and the affected entities",
       "mappings"),
)

# --- Surrounding Program Context ---------------------------------------------
_SURROUNDING = (
    _e("enclosing_context", "the enclosing type or component context", "enclosing"),
    _e("file_module_context", "relevant file and module context", "modules"),
    _e("callers_callees", "relevant callers and callees", "callers", "callees"),
    _e("related_entities", "related program entities", "entities"),
    _e("repository_context", "relevant repository organization and development context",
       "context_files"),
    _e("surrounding_provenance", "contextual-analysis provenance and diagnostics",
       "analysis_component", "input_artifacts"),
)

# --- Standalone Artifact Requirements ----------------------------------------
_STANDALONE = (
    _e("source_artifacts", "non-function artifacts affected by or required for the change",
       "artifacts"),
    _e("target_artifacts", "corresponding artifacts in the target repository", "artifacts"),
    _e("artifact_change_relation", "relationships between the artifacts and the transformation",
       "relationships"),
    _e("artifact_differences", "relevant artifact differences", "differences"),
    _e("artifact_locations", "artifact locations and types", "locations", "types"),
    _e("artifact_provenance", "artifact-analysis provenance and diagnostics",
       "analysis_component", "input_artifacts"),
)


# --- Artifact Placement (standalone change type) -----------------------------
_ARTIFACT_PLACEMENT = (
    _e("target_repo_revision", "target repository and revision identifiers",
       "repo", "revision"),
    _e("target_location", "the verified path the artifact takes in the target",
       "target_path"),
    _e("placement_basis", "why that location is the correct one",
       "basis", "existing_siblings"),
    _e("conflicting_artifact", "any target artifact already occupying the location",
       "conflict"),
    _e("placement_provenance", "placement-analysis provenance and diagnostics",
       "analysis_component", "input_artifacts"),
)


CATEGORY_ELEMENTS: dict[Category, tuple[ElementSpec, ...]] = {
    Category.SOURCE_CHANGE: _SOURCE_CHANGE,
    Category.TARGET_LOCALIZATION: _TARGET_LOCALIZATION,
    Category.FUNCTION_TRANSFORMATION: _FUNCTION_TRANSFORMATION,
    Category.STRUCTURAL: _STRUCTURAL,
    Category.REFACTORING: _REFACTORING,
    Category.COMPATIBILITY: _COMPATIBILITY,
    Category.VERIFICATION: _VERIFICATION,
    Category.SURROUNDING: _SURROUNDING,
    Category.STANDALONE: _STANDALONE,
    Category.ARTIFACT_PLACEMENT: _ARTIFACT_PLACEMENT,
}


def elements_for(category: Category) -> tuple[ElementSpec, ...]:
    """The required information elements of a category (``m_i`` in the spec)."""
    return CATEGORY_ELEMENTS[category]


def element_spec(category: Category, element_id: str) -> ElementSpec:
    for spec in CATEGORY_ELEMENTS[category]:
        if spec.element_id == element_id:
            return spec
    raise KeyError(f"{category.value} has no required element {element_id!r}")
