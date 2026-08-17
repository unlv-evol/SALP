"""Schema conformance of a *written* SAP, and regression of the scoring.

`validation.py` checks the in-memory SAP before characterization; the schema
validator checks what actually lands on disk, which is what a downstream
consumer reads. These tests corrupt a conformant package in specific ways and
assert each corruption is caught -- a validator that never fails is worthless.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.test_ingest_and_pipeline import _make_fixture

from salp.characterization import Characterizer
from salp.models import Category, CategoryEvidence, ChangeType, EvidenceObject, EvidenceState
from salp.packaging import validate_output, validate_pr_dir, validate_sap_dir

from salp.config import Config  # isort: skip
from salp.pipeline import run  # isort: skip


@pytest.fixture
def written(tmp_path: Path) -> Path:
    """A complete, conformant run on disk."""
    _make_fixture(tmp_path, hunks=2)
    cfg = Config()
    cfg.paths.gacpd_run = tmp_path
    cfg.paths.output = tmp_path / "out"
    assert run(cfg) == 1
    return cfg.paths.output


@pytest.fixture
def sap_dir(written: Path) -> Path:
    return next(written.glob("*/PR-*/sap-*"))


# --- the happy path -----------------------------------------------------------
def test_a_written_run_is_conformant(written: Path):
    report = validate_output(written)
    assert report.ok, report.errors
    assert report.checked == 1


def test_an_empty_output_directory_is_reported(tmp_path: Path):
    report = validate_output(tmp_path)
    assert not report.ok
    assert "no pull-request groupings" in report.errors[0]


# --- corruptions the validator must catch -------------------------------------
def test_a_missing_required_file_is_caught(sap_dir: Path):
    (sap_dir / "characterization.json").unlink()
    assert not validate_sap_dir(sap_dir).ok


def test_a_missing_hunk_payload_is_caught(sap_dir: Path):
    (sap_dir / "hunks" / "H-1" / "hunk.diff").unlink()
    report = validate_sap_dir(sap_dir)
    assert any("hunk.diff" in e for e in report.errors)


def test_malformed_json_is_caught(sap_dir: Path):
    (sap_dir / "sap.json").write_text("{ not json")
    assert not validate_sap_dir(sap_dir).ok


def test_a_dangling_index_reference_is_caught(sap_dir: Path):
    index_path = sap_dir / "hunks" / "H-1" / "hunk.json"
    index = json.loads(index_path.read_text())
    index["evidence"]["source_change"]["ref"] = "does_not_exist.json"
    index_path.write_text(json.dumps(index))
    report = validate_sap_dir(sap_dir)
    assert any("does not resolve" in e for e in report.errors)


def test_an_invalid_evidence_state_is_caught(sap_dir: Path):
    doc_path = sap_dir / "hunks" / "H-1" / "localization.json"
    doc = json.loads(doc_path.read_text())
    doc["elements"][0]["state"] = "MAYBE"
    doc_path.write_text(json.dumps(doc))
    assert any("invalid state" in e for e in validate_sap_dir(sap_dir).errors)


def test_an_element_without_provenance_is_caught(sap_dir: Path):
    doc_path = sap_dir / "hunks" / "H-1" / "localization.json"
    doc = json.loads(doc_path.read_text())
    doc["elements"][0]["provenance"] = None
    doc_path.write_text(json.dumps(doc))
    assert any("no provenance" in e for e in validate_sap_dir(sap_dir).errors)


def test_a_non_present_foundational_category_is_caught(sap_dir: Path):
    index_path = sap_dir / "hunks" / "H-1" / "hunk.json"
    index = json.loads(index_path.read_text())
    index["evidence"]["function_transformation"]["state"] = "UNAVAILABLE"
    index_path.write_text(json.dumps(index))
    report = validate_sap_dir(sap_dir)
    assert any("must be PRESENT" in e for e in report.errors)


def test_an_incomplete_hunk_ordering_is_caught(sap_dir: Path):
    change_path = sap_dir / "change.json"
    change = json.loads(change_path.read_text())
    change["hunk_order"] = change["hunk_order"][:1]
    change_path.write_text(json.dumps(change))
    assert any("does not cover the hunks" in e for e in validate_sap_dir(sap_dir).errors)


def test_a_characterization_missing_a_hunk_is_caught(sap_dir: Path):
    profile_path = sap_dir / "characterization.json"
    profile = json.loads(profile_path.read_text())
    profile["hunks"].pop("H-2")
    profile_path.write_text(json.dumps(profile))
    assert any("profile for every hunk" in e for e in validate_sap_dir(sap_dir).errors)


def test_a_dangling_context_file_is_caught(written: Path):
    pr_dir = next(written.glob("*/PR-*"))
    manifest_path = pr_dir / "pr.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["context_files"][0]["path"] = "_context/absent.java"
    manifest_path.write_text(json.dumps(manifest))
    assert any("does not exist" in e for e in validate_pr_dir(pr_dir).errors)


def test_a_cross_file_edge_naming_no_sap_is_caught(written: Path):
    pr_dir = next(written.glob("*/PR-*"))
    manifest_path = pr_dir / "pr.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["cross_file_relationships"] = [
        {"rel": "co_change", "from": "RC-999-Ghost#H-1", "to": "RC-999-Other#H-1"}
    ]
    manifest_path.write_text(json.dumps(manifest))
    assert any("names no SAP here" in e for e in validate_pr_dir(pr_dir).errors)


# --- regression: the scoring must not drift silently --------------------------
def _fixed_evidence() -> dict[Category, CategoryEvidence]:
    """A hand-built SAP whose expected profile is pinned below."""
    def cat(c: Category, *pairs: tuple[EvidenceState, float]) -> CategoryEvidence:
        return CategoryEvidence(
            category=c,
            elements=[
                EvidenceObject(
                    object_id=f"{c.value}-{i}", object_type=f"{c.value}.e{i}",
                    state=s, representation=r,
                )
                for i, (s, r) in enumerate(pairs)
            ],
        )

    P, VA, U, NA = (
        EvidenceState.PRESENT, EvidenceState.VERIFIED_ABSENT,
        EvidenceState.UNAVAILABLE, EvidenceState.NOT_APPLICABLE,
    )
    return {
        Category.SOURCE_CHANGE: cat(Category.SOURCE_CHANGE, (P, 1.0), (P, 0.5)),
        Category.TARGET_LOCALIZATION: cat(Category.TARGET_LOCALIZATION, (P, 1.0), (VA, 1.0)),
        Category.FUNCTION_TRANSFORMATION: cat(Category.FUNCTION_TRANSFORMATION, (P, 1.0)),
        Category.STRUCTURAL: cat(Category.STRUCTURAL, (P, 1.0), (U, 1.0)),
        Category.REFACTORING: cat(Category.REFACTORING, (VA, 1.0)),
        Category.COMPATIBILITY: cat(Category.COMPATIBILITY, (U, 1.0)),
        Category.VERIFICATION: cat(Category.VERIFICATION, (P, 1.0)),
        Category.ARTIFACT_PLACEMENT: cat(Category.ARTIFACT_PLACEMENT, (NA, 1.0)),
    }


# Derived by hand from the specification formulas, then confirmed against the
# engine -- not copied from its output. A change to these numbers is a change to
# the scoring semantics and must be justified, not absorbed.
#
#   Coverage = (3·1 + 3·1 + 3·1 + 2·0.5 + 2·1 + 2·0 + 2·1) / 17 = 14/17
#   Fidelity = (3·0.75 + 3·1 + 3·1 + 2·1 + 2·1) / 13           = 12.25/13
#   Readiness = harmonic mean of the two; no foundational cap applies, because
#   source_change's F = 0.75 is not *below* the 0.75 moderate threshold.
#
# artifact_placement is wholly NOT_APPLICABLE and appears in neither denominator.
EXPECTED = {
    "coverage_score": 14 / 17,
    "fidelity_score": 12.25 / 13,
    "readiness_base": 0.8789237668161434,
    "coverage_level": "SUBSTANTIAL",
    "fidelity_level": "VERY_RICH",
    "readiness_preliminary": "HIGH",
    "readiness_final": "HIGH",
}


def test_characterization_matches_the_pinned_profile():
    profile = Characterizer().characterize(_fixed_evidence(), ChangeType.MAPPED)
    actual = profile.model_dump(mode="json")
    for key, value in EXPECTED.items():
        assert actual[key] == pytest.approx(value) if isinstance(value, float) else (
            actual[key] == value
        ), f"{key}: {actual[key]!r} != {value!r}"


def test_characterization_is_reproducible():
    """Independent recomputation must yield identical results."""
    first = Characterizer().characterize(_fixed_evidence(), ChangeType.MAPPED)
    second = Characterizer().characterize(_fixed_evidence(), ChangeType.MAPPED)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
