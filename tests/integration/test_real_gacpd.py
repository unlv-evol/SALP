"""End-to-end checks against the real GACPD sample under ``data/gacpd/``.

The sample is git-ignored, so these skip wherever it is not present. They guard
the parsers against the formats GACPD actually emits -- CRLF endings, blank
fields, per-threshold similarity, truncated diff section headings -- which the
synthetic fixture can only approximate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from salp.config import Config
from salp.ingest import discover_pull_requests
from salp.packaging import validate_sap
from salp.pipeline import run

DATA = Path(__file__).resolve().parents[2] / "data" / "gacpd"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATA.is_dir() or not any(DATA.rglob("pr_results.txt")),
        reason="no GACPD sample under data/gacpd/",
    ),
]


@pytest.fixture(scope="module")
def pull_requests():
    return {pr.metadata.number: pr for pr in discover_pull_requests(DATA)}


@pytest.fixture(scope="module")
def output(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("real-run")
    cfg = Config()
    cfg.paths.gacpd_run = DATA
    cfg.paths.output = out
    assert run(cfg) > 0
    return out


# --- ingestion ----------------------------------------------------------------
def test_every_pull_request_recovers_its_repository_pair(pull_requests):
    """The pair comes from the per-file records, not the abbreviated run directory."""
    for number, pr in pull_requests.items():
        m = pr.metadata
        assert m.source_repo and m.target_repo, f"PR {number} recovered no repository pair"
        assert "/" in m.source_repo and "/" in m.target_repo
        assert m.divergence_date and m.cutoff_date, f"PR {number} has no dates"


def test_blank_pr_fields_degrade_to_none_with_a_diagnostic(pull_requests):
    """PR 2731 leaves title and location blank; neither may swallow the next line."""
    pr = pull_requests.get("2731")
    if pr is None:
        pytest.skip("PR 2731 not in the sample")
    assert pr.metadata.title is None
    assert pr.metadata.url is None
    assert pr.metadata.divergence_date == "2022-09-08"
    assert any("no title" in d for d in pr.metadata.diagnostics)


def test_mo_files_recover_paths_and_extensions(pull_requests):
    for pr in pull_requests.values():
        for mo in pr.mo_files:
            assert mo.ext != "txt", f"{mo.display_name} fell back to the default extension"
            assert "/" in mo.source_path, f"{mo.display_name} has no repository-relative path"
            assert mo.localization.divergent_path
            # the located path must be repo-relative, not GACPD's working directory
            assert not mo.localization.divergent_path.startswith("Results/")


def test_scala_and_java_are_both_ingested(pull_requests):
    exts = {mo.ext for pr in pull_requests.values() for mo in pr.mo_files}
    assert {"java", "scala"} <= exts


def test_weak_alignment_reads_as_reduced_confidence(pull_requests):
    """The CombinedKey deletion block matches only at the coarsest threshold."""
    pr = pull_requests.get("12535")
    if pr is None:
        pytest.skip("PR 12535 not in the sample")
    (mo,) = pr.mo_files
    assert mo.display_name == "CombinedKey.java"
    assert mo.localization.confidence("H-1") == pytest.approx(1 / 3)
    breakdown = mo.localization.breakdown("H-1")
    assert breakdown["deletions"] == {50: 0.0, 40: 0.0, 30: 1.0}
    assert breakdown["additions"] == {50: 0.0, 40: 0.0, 30: 0.0}


# --- construction -------------------------------------------------------------
def test_hunks_sharing_a_function_share_one_pool_entry(output):
    """KafkaClusterTestKit edits close() three times across its five hunks."""
    sap_dir = output / "linkedinKafka-apacheKafka" / "PR-12538" / "sap-KafkaClusterTestKit"
    if not sap_dir.is_dir():
        pytest.skip("PR 12538 not in the sample")

    manifest = json.loads((sap_dir / "sap.json").read_text())
    assert len(manifest["hunks"]) == 5
    assert len(manifest["functions"]) == 3, "five hunks must collapse onto three functions"

    owners = {
        h: json.loads((sap_dir / "hunks" / h / "hunk.json").read_text())["transformation"][
            "f_s_before"
        ]
        for h in manifest["hunks"]
    }
    assert owners["H-3"] == owners["H-4"] == owners["H-5"]
    assert "KafkaClusterTestKit_close" in owners["H-3"]


def test_a_truncated_section_heading_is_not_named_as_a_function(output):
    """H-1's heading is a continuation line, so its entry stays positional."""
    sap_dir = output / "linkedinKafka-apacheKafka" / "PR-12538" / "sap-KafkaClusterTestKit"
    if not sap_dir.is_dir():
        pytest.skip("PR 12538 not in the sample")
    names = {p.name for p in (sap_dir / "functions").iterdir()}
    assert "KafkaClusterTestKit_fn1" in names
    assert not any("MetadataRecordSerde" in n for n in names)


def test_pure_deletion_hunk_recovers_its_post_change_side(output):
    """WalletFiles H-1 only deletes, so GACPD emits no full_add; the diff supplies it."""
    sap_dir = output / "langerhansDogecoinjNew-bitcoinjBitcoinj" / "PR-2731" / "sap-WalletFiles"
    if not sap_dir.is_dir():
        pytest.skip("PR 2731 not in the sample")
    doc = json.loads((sap_dir / "hunks" / "H-1" / "transformation.json").read_text())
    unit = next(e for e in doc["elements"] if e["element"].endswith("transformation_unit"))
    assert unit["state"] == "PRESENT"
    assert unit["representation"] == 1.0, "tau must be complete once the side is reconstructed"
    assert "reconstructed from the hunk diff" in unit["attributes"]["derivation"]


def test_every_sap_is_valid_and_every_reference_resolves(output):
    sap_dirs = sorted(output.glob("*/PR-*/sap-*"))
    assert sap_dirs
    for sap_dir in sap_dirs:
        manifest = json.loads((sap_dir / "sap.json").read_text())
        for hunk_id in manifest["hunks"]:
            index = json.loads((sap_dir / "hunks" / hunk_id / "hunk.json").read_text())
            refs = [v for v in index["transformation"].values() if isinstance(v, str)]
            refs += [e["ref"] for e in index["evidence"].values() if e.get("ref")]
            for ref in refs:
                resolved = sap_dir / ref
                if not resolved.exists():
                    resolved = sap_dir / "hunks" / hunk_id / ref
                assert resolved.is_file(), f"{sap_dir.name}/{hunk_id}: dangling {ref}"


def test_construction_reports_no_validation_errors(pull_requests):
    from salp.packaging import build_sap

    for number, pr in pull_requests.items():
        for mo in pr.mo_files:
            sap = build_sap(mo, sap_id=f"RC-{number}-{mo.display_name}", pr=pr)
            assert validate_sap(sap) == [], f"PR {number} / {mo.display_name}"


def test_context_files_referenced_by_the_manifest_exist(output):
    for manifest_path in sorted(output.glob("*/PR-*/pr.json")):
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["context_files"]:
            if entry["path"] is None:
                # GACPD retained no copyable payload; the gap must be explained
                assert entry["diagnostics"]
            else:
                assert (manifest_path.parent / entry["path"]).is_file()


# --- characterization ---------------------------------------------------------
def test_enrichment_raises_readiness_for_parseable_files(output):
    """With structure, compatibility, and verification recovered, Java SAPs reach High.

    Scala files have no configured grammar, so their enrichment categories stay
    UNAVAILABLE and they cap lower -- an explicit gap, not a silent skip.
    """
    profiles = sorted(output.glob("*/PR-*/sap-*/characterization.json"))
    assert profiles

    levels = {"LOW": 0, "MODERATE": 1, "HIGH": 2}
    java_saps, other = [], []
    for path in profiles:
        profile = json.loads(path.read_text())
        manifest = json.loads((path.parent / "sap.json").read_text())
        (java_saps if manifest["source_file"].endswith(".java") else other).append(
            (path.parent.name, profile)
        )

    assert java_saps, "the sample should contain Java SAPs"
    for name, profile in java_saps:
        assert profile["aggregate"]["readiness"] == "HIGH", name
        for hunk_id, hunk in profile["hunks"].items():
            assert hunk["applied_constraints"] == [], f"{name}/{hunk_id}"
            assert hunk["coverage_score"] > 0.8, f"{name}/{hunk_id}"

    for name, profile in other:
        # unrecoverable enrichment must lower Readiness, not be ignored
        assert levels[profile["aggregate"]["readiness"]] < levels["HIGH"], name


def test_foundational_categories_are_complete_everywhere(output):
    for path in sorted(output.glob("*/PR-*/sap-*/characterization.json")):
        profile = json.loads(path.read_text())
        for hunk_id, hunk in profile["hunks"].items():
            for foundational in (
                "source_change", "target_localization", "function_transformation"
            ):
                assert hunk["category_scores"][foundational]["coverage"] == 1.0, (
                    f"{path.parent.name}/{hunk_id}/{foundational}"
                )


def test_no_blocking_conflict_is_raised_from_build_file_text_alone(output):
    """A textual scan of build files cannot establish an irreconcilable conflict."""
    for path in sorted(output.glob("*/PR-*/sap-*/hunks/*/compatibility.json")):
        doc = json.loads(path.read_text())
        assert doc["blocking_conflict"] is False, path


def test_verification_reports_real_target_tests(output):
    """At least one SAP should find a covering test in the target repository."""
    found = []
    for path in sorted(output.glob("*/PR-*/sap-*/hunks/*/verification.json")):
        doc = json.loads(path.read_text())
        tests = next(
            (e for e in doc["elements"] if e["element"].endswith("covering_tests")), None
        )
        if tests and tests["state"] == "PRESENT":
            found += tests["attributes"]["tests"]
    assert found, "no covering test was discovered across the whole sample"
    assert all("test" in p.lower() for p in found)
