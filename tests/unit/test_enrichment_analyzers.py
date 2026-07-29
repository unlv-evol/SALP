"""Compatibility, verification, and refactoring analyzers.

These three recover evidence from outside the GACPD output: the imports and
build files of both pinned states, the target repository's tests, and
RefactoringMiner. Each must degrade to an explicit UNAVAILABLE when its input is
missing, and to VERIFIED_ABSENT when its investigation completes and finds
nothing.
"""

from __future__ import annotations

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
_RENAME = {
    "type": "Rename Method",
    "description": "Rename Method save() to persist()",
    "leftSideLocations": [{"filePath": "core/src/main/java/Wallet.java", "codeElement": "save()"}],
    "rightSideLocations": [
        {"filePath": "core/src/main/java/Wallet.java", "codeElement": "persist()"}
    ],
}
_ELSEWHERE = {
    "type": "Extract Method",
    "description": "Extract Method in another file",
    "leftSideLocations": [{"filePath": "core/src/main/java/Other.java", "codeElement": "a()"}],
    "rightSideLocations": [],
}


def test_refactorings_are_filtered_to_this_file():
    ctx = _ctx(refactorings=[_RENAME, _ELSEWHERE])
    ctx.target_path = "core/src/main/java/Wallet.java"
    ce = RefactoringAnalyzer().investigate(ctx)

    reported = _by_element(ce, "refactorings").attributes["refactorings"]
    assert [r["type"] for r in reported] == ["Rename Method"]
    assert set(_by_element(ce, "affected_entities").attributes["entities"]) == {
        "save()", "persist()"
    }
    mapping = _by_element(ce, "entity_mappings").attributes["mappings"][0]
    assert (mapping["source"], mapping["target"]) == ("save()", "persist()")


def test_a_completed_run_touching_nothing_is_verified_absent():
    ctx = _ctx(refactorings=[_ELSEWHERE])
    ctx.target_path = "core/src/main/java/Wallet.java"
    ce = RefactoringAnalyzer().investigate(ctx)
    assert all(e.state is EvidenceState.VERIFIED_ABSENT for e in ce.elements)


def test_an_unconfigured_tool_is_unavailable_with_guidance():
    ce = RefactoringAnalyzer().investigate(_ctx())
    assert all(e.state is EvidenceState.UNAVAILABLE for e in ce.elements)
    assert "refactoringminer_jar" in ce.elements[0].provenance.diagnostics


def test_a_failed_run_reports_its_own_diagnostic():
    ce = RefactoringAnalyzer().investigate(_ctx(refactorings="RefactoringMiner exited 1: boom"))
    assert all(e.state is EvidenceState.UNAVAILABLE for e in ce.elements)
    assert "boom" in ce.elements[0].provenance.diagnostics
