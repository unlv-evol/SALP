"""Compatibility, verification, and refactoring analyzers.

These three recover evidence from outside the GACPD output: the imports and
build files of both pinned states, the target repository's tests, and
RefactoringMiner. Each must degrade to an explicit UNAVAILABLE when its input is
missing, and to VERIFIED_ABSENT when its investigation completes and finds
nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from salp.analyzers import (
    AnalysisContext,
    CompatibilityAnalyzer,
    RefactoringAnalyzer,
    VerificationAnalyzer,
)
from salp.models import EvidenceState
from salp.structural import TREE_SITTER_AVAILABLE

SOURCE = """package org.example.wallet;

import java.util.Objects;
import org.example.wallet.internal.Helper;
import com.google.common.collect.ImmutableList;
import org.slf4j.Logger;

public class Wallet {
    void save() {}
}
"""

TARGET = """package org.example.wallet;

import java.util.Objects;
import org.example.wallet.internal.Helper;

public class Wallet {
    void save() {}
}
"""


def _ctx(**extras: object) -> AnalysisContext:
    return AnalysisContext(
        hunk_id="H-1",
        fn_id="Wallet",
        source_file="core/src/main/java/org/example/wallet/Wallet.java",
        ext="java",
        source_file_text=SOURCE,
        target_file_text=TARGET,
        extras=dict(extras),
    )


def _by_element(evidence, suffix: str):
    return next(e for e in evidence.elements if e.object_type.endswith(suffix))


# --- compatibility ------------------------------------------------------------
pytestmark_ts = pytest.mark.skipif(
    not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed"
)


@pytestmark_ts
def test_compatibility_diffs_imports_between_the_two_pinned_files():
    ce = CompatibilityAnalyzer().investigate(
        _ctx(source_dependencies=["com.google.guava:guava:31"], target_dependencies=[])
    )
    mappings = _by_element(ce, "api_mappings")
    assert mappings.state is EvidenceState.PRESENT
    introduced = {m["api"] for m in mappings.attributes["mappings"]}
    # only what the source has and the target lacks
    assert any("ImmutableList" in a for a in introduced)
    assert any("slf4j" in a for a in introduced)
    assert not any("Objects" in a for a in introduced)


@pytestmark_ts
def test_first_party_imports_are_not_reported_as_dependencies():
    """org.example.wallet.* is the file's own project, not a third-party API."""
    ce = CompatibilityAnalyzer().investigate(
        _ctx(source_dependencies=[], target_dependencies=["org.slf4j:slf4j-api:1.7"])
    )
    findings = _by_element(ce, "compatibility_findings")
    reported = {f["api"] for f in findings.attributes["findings"]}
    assert not any(a.startswith("org.example.wallet") for a in reported)
    assert any("com.google" in a for a in reported)


@pytestmark_ts
def test_declared_dependencies_are_recognised():
    ce = CompatibilityAnalyzer().investigate(
        _ctx(source_dependencies=[], target_dependencies=["org.slf4j:slf4j-api:1.7.30"])
    )
    findings = {
        f["api"]: f["dependency_declared"]
        for f in _by_element(ce, "compatibility_findings").attributes["findings"]
    }
    assert findings["org.slf4j.Logger"] is True
    assert findings["com.google.common.collect.ImmutableList"] is False


@pytestmark_ts
def test_compatibility_never_raises_a_blocking_conflict_from_build_text():
    ce = CompatibilityAnalyzer().investigate(
        _ctx(source_dependencies=[], target_dependencies=[])
    )
    assert not any(e.blocking_conflict for e in ce.elements)


@pytestmark_ts
def test_unknown_target_dependencies_are_unavailable_not_empty():
    ce = CompatibilityAnalyzer().investigate(_ctx())
    assert _by_element(ce, "target_dependencies").state is EvidenceState.UNAVAILABLE
    assert _by_element(ce, "compatibility_findings").state is EvidenceState.UNAVAILABLE


def test_compatibility_without_a_source_file_is_unavailable():
    ctx = AnalysisContext(hunk_id="H-1", fn_id="X", source_file="X.java", ext="java")
    ce = CompatibilityAnalyzer().investigate(ctx)
    assert all(e.state is EvidenceState.UNAVAILABLE for e in ce.elements)


def test_compatibility_on_an_unparseable_language_is_unavailable():
    ctx = AnalysisContext(
        hunk_id="H-1", fn_id="X", source_file="X.scala", ext="scala", source_file_text="object X"
    )
    ce = CompatibilityAnalyzer().investigate(ctx)
    assert all(e.state is EvidenceState.UNAVAILABLE for e in ce.elements)
    assert "scala" in ce.elements[0].provenance.diagnostics


# --- verification -------------------------------------------------------------
def test_verification_records_the_tests_it_found():
    ce = VerificationAnalyzer().investigate(
        _ctx(covering_tests=["core/src/test/java/WalletTest.java"], target_entity="Wallet")
    )
    assert _by_element(ce, "covering_tests").state is EvidenceState.PRESENT
    mapping = _by_element(ce, "test_entity_mapping").attributes["mappings"]
    assert mapping == [{"test": "core/src/test/java/WalletTest.java", "entity": "Wallet"}]
    # SALP does not build or run the target suite
    assert _by_element(ce, "pre_adaptation_status").state is EvidenceState.UNAVAILABLE


def test_a_completed_search_finding_no_test_is_verified_absent():
    """No oracle is a finding, not a gap: verified absence takes full Coverage."""
    ce = VerificationAnalyzer().investigate(_ctx(covering_tests=[], target_entity="Wallet"))
    assert all(e.state is EvidenceState.VERIFIED_ABSENT for e in ce.elements)


def test_an_unsearchable_target_is_unavailable_not_absent():
    ce = VerificationAnalyzer().investigate(_ctx())
    assert all(e.state is EvidenceState.UNAVAILABLE for e in ce.elements)
    assert "fetch-repos" in ce.elements[0].provenance.diagnostics


# --- refactoring --------------------------------------------------------------
# Shaped as RefactoringMiner actually reports: commits, each with refactorings,
# each with parallel-ish location arrays.
_RENAME = {
    "type": "Rename Method",
    "description": "Rename Method save() to persist()",
    "markup": "Rename Method <b>save()</b>",
    "leftSideLocations": [
        {"filePath": "core/src/main/java/Wallet.java", "codeElement": "save()",
         "startLine": 10, "endLine": 12},
    ],
    "rightSideLocations": [
        {"filePath": "core/src/main/java/Wallet.java", "codeElement": "persist()",
         "startLine": 10, "endLine": 12},
    ],
}
_ELSEWHERE = {
    "type": "Extract Method",
    "description": "Extract Method in another file",
    "leftSideLocations": [{"filePath": "core/src/main/java/Other.java", "codeElement": "a()"}],
    "rightSideLocations": [],
}
# 2 left, 1 right: the arrays are not counterparts, which is the case that
# breaks naive positional pairing.
_UNCORRELATED = {
    "type": "Inline Variable",
    "description": "Inline Variable x in Wallet",
    "leftSideLocations": [
        {"filePath": "core/src/main/java/Wallet.java", "codeElement": "x"},
        {"filePath": "core/src/main/java/Wallet.java", "codeElement": "y"},
    ],
    "rightSideLocations": [
        {"filePath": "core/src/main/java/Wallet.java", "codeElement": "inlined"},
    ],
}
_MOVED = {
    "type": "Move Class",
    "description": "Move Class Wallet moved to wallet.core",
    "leftSideLocations": [{"filePath": "core/src/main/java/Wallet.java", "codeElement": "Wallet"}],
    "rightSideLocations": [
        {"filePath": "core/src/main/java/wallet/core/Wallet.java", "codeElement": "Wallet"}
    ],
}


def _commits(*refactorings: dict) -> tuple[dict, ...]:
    return ({"sha1": "abc123", "url": "https://example/commit/abc123",
             "repository": "acme/target", "refactorings": list(refactorings)},)


def _ref_ctx(*refactorings: dict) -> AnalysisContext:
    ctx = _ctx(refactorings=_commits(*refactorings))
    ctx.target_path = "core/src/main/java/Wallet.java"
    return ctx


def test_refactorings_are_filtered_to_this_file():
    ce = RefactoringAnalyzer().investigate(_ref_ctx(_RENAME, _ELSEWHERE))
    reported = _by_element(ce, "refactorings").attributes["refactorings"]
    assert [r["type"] for r in reported] == ["Rename Method"]
    assert set(_by_element(ce, "affected_entities").attributes["entities"]) == {
        "save()", "persist()"
    }


def test_a_one_to_one_refactoring_is_correlated_into_a_mapping():
    ce = RefactoringAnalyzer().investigate(_ref_ctx(_RENAME))
    (mapping,) = _by_element(ce, "entity_mappings").attributes["mappings"]
    assert mapping["correlated"] is True
    assert mapping["source"]["element"] == "save()"
    assert mapping["target"]["element"] == "persist()"


def test_mismatched_location_arrays_are_not_paired():
    """Half of a real run has arrays of different lengths; pairing them would
    assert a correspondence RefactoringMiner never reported."""
    ce = RefactoringAnalyzer().investigate(_ref_ctx(_UNCORRELATED))
    mappings = _by_element(ce, "entity_mappings").attributes["mappings"]
    assert all(m["correlated"] is False for m in mappings)
    # every location is still reported, just unpaired
    assert len(mappings) == 3
    assert {m["source"]["element"] for m in mappings if m["source"]} == {"x", "y"}


def test_the_commit_is_retained_for_traceability():
    ce = RefactoringAnalyzer().investigate(_ref_ctx(_RENAME))
    (reported,) = _by_element(ce, "refactorings").attributes["refactorings"]
    assert reported["commit"] == "abc123"
    assert reported["commit_url"].endswith("abc123")
    assert reported["markup"]  # RefactoringMiner's highlighted description


def test_a_file_level_move_relocates_the_landing_site():
    """The reference implementation skipped these; for a reusable change they are
    the most consequential refactorings there are."""
    ce = RefactoringAnalyzer().investigate(_ref_ctx(_MOVED))
    (reported,) = _by_element(ce, "refactorings").attributes["refactorings"]
    assert reported["relocates_landing_site"] is True
    (edge,) = _by_element(ce, "refactoring_change_relation").attributes["relationships"]
    assert edge["rel"] == "landing_site_relocated_by"


def test_a_completed_run_touching_nothing_is_verified_absent():
    ce = RefactoringAnalyzer().investigate(_ref_ctx(_ELSEWHERE))
    assert all(e.state is EvidenceState.VERIFIED_ABSENT for e in ce.elements)


def test_no_drift_between_the_pinned_states_is_verified_absent():
    """An empty report is an absence of refactoring, not an absence of evidence."""
    ce = RefactoringAnalyzer().investigate(_ctx(refactorings=()))
    assert all(e.state is EvidenceState.VERIFIED_ABSENT for e in ce.elements)


def test_an_unconfigured_tool_is_unavailable_with_guidance():
    ce = RefactoringAnalyzer().investigate(_ctx())
    assert all(e.state is EvidenceState.UNAVAILABLE for e in ce.elements)
    assert "refactoringminer_jar" in ce.elements[0].provenance.diagnostics


def test_a_failed_run_reports_its_own_diagnostic():
    ce = RefactoringAnalyzer().investigate(_ctx(refactorings="RefactoringMiner exited 1: boom"))
    assert all(e.state is EvidenceState.UNAVAILABLE for e in ce.elements)
    assert "boom" in ce.elements[0].provenance.diagnostics


def test_the_installed_distribution_version_is_recorded():
    from salp.analyzers.tools import refactoring_miner_version

    dist = Path("tools/refactoringminer/RefactoringMiner-3.1.4/bin/RefactoringMiner")
    assert refactoring_miner_version(dist) == "3.1.4"
    assert refactoring_miner_version(None) is None


# --- tool versions in provenance ----------------------------------------------
def test_tool_backed_analyzers_record_the_version_in_use():
    """Evidence is reproducible only under fixed tool versions.

    The version is resolved from the installed tool rather than hardcoded, so it
    cannot drift from what actually produced the evidence.
    """
    from salp.analyzers import (
        CompatibilityAnalyzer,
        StructuralAnalyzer,
        SurroundingAnalyzer,
        VerificationAnalyzer,
    )
    from salp.analyzers.tools import git_version, tree_sitter_version

    expected = {
        StructuralAnalyzer: tree_sitter_version(),
        SurroundingAnalyzer: tree_sitter_version(),
        CompatibilityAnalyzer: tree_sitter_version(),
        VerificationAnalyzer: git_version(),
    }
    for analyzer_cls, version in expected.items():
        assert analyzer_cls().tool_version() == version, analyzer_cls.__name__


@pytestmark_ts
def test_the_recorded_version_reaches_the_evidence_object():
    from salp.analyzers.tools import tree_sitter_version

    ce = CompatibilityAnalyzer().investigate(_ctx(target_dependencies=[]))
    versions = {e.provenance.analysis_version for e in ce.elements if e.provenance}
    assert versions == {tree_sitter_version()}
    assert None not in versions


def test_refactoringminer_version_comes_from_a_version_file(tmp_path):
    from salp.analyzers.tools import refactoring_miner_version

    jar = tmp_path / "RefactoringMiner.jar"
    jar.write_text("")
    assert refactoring_miner_version(jar) is None  # no VERSION file yet
    (tmp_path / "VERSION").write_text("3.0.9\n")
    assert refactoring_miner_version(tmp_path / "other.jar") == "3.0.9"
    assert refactoring_miner_version(None) is None
