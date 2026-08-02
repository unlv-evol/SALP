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
from salp.structural import grammar_for

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
    doc = json.loads((sap_dir / "hunks" / "H-1" / "edit_region.json").read_text())
    post = next(e for e in doc["elements"] if e["element"].endswith("post_change_function"))
    assert post["state"] == "PRESENT", "the omitted side must be reconstructed from the diff"
    assert (sap_dir / post["attributes"]["payload_ref"]).is_file()


def test_an_import_region_edit_has_no_function_transformation(output):
    """WalletFiles H-1 edits the import block, which has no enclosing function.

    tau is undefined there rather than unrecovered, so it must be NOT_APPLICABLE
    -- which leaves both denominators -- and not UNAVAILABLE, which would score
    the hunk down for evidence it structurally cannot have.
    """
    sap_dir = output / "langerhansDogecoinjNew-bitcoinjBitcoinj" / "PR-2731" / "sap-WalletFiles"
    if not sap_dir.is_dir():
        pytest.skip("PR 2731 not in the sample")
    doc = json.loads((sap_dir / "hunks" / "H-1" / "transformation.json").read_text())
    unit = next(e for e in doc["elements"] if e["element"].endswith("transformation_unit"))
    assert unit["state"] == "NOT_APPLICABLE"
    assert "import block" in unit["provenance"]["diagnostics"]


def test_tau_is_three_functions_not_regions_or_files(output):
    """Every member of a recovered tau must be a function body.

    f_s and f'_s were hunk regions in diff syntax and f_t was the whole enclosing
    file; none of the three was what the specification asks for.
    """
    checked = 0
    for path in sorted(output.glob("*/PR-*/sap-*/hunks/*/transformation.json")):
        doc = json.loads(path.read_text())
        unit = next(e for e in doc["elements"] if e["element"].endswith("transformation_unit"))
        if unit["state"] != "PRESENT":
            continue
        sap_dir = path.parent.parent.parent
        for member in ("f_s_before", "f_s_after", "f_t"):
            ref = unit["attributes"].get(member)
            if ref is None:
                continue
            text = (sap_dir / ref).read_text()
            assert not text.lstrip().startswith("@@"), f"{path}: {member} is a diff region"
            assert "\n-" not in text and "\n+" not in text, f"{path}: {member} carries diff markers"
            assert "package " not in text.split("\n", 1)[0], f"{path}: {member} is a whole file"
            checked += 1
    assert checked, "no complete transformation unit was produced"


def test_the_target_function_is_matched_by_signature_not_line_number(output):
    """f_t must be the counterpart of f_s, not whatever occupies the same lines.

    Reusing the source's line span against a diverged variant resolved `saveNow`
    to `saveNowInternal` and reported it PRESENT.
    """
    for path in sorted(output.glob("*/PR-*/sap-*/functions/*/structure.json")):
        elements = {
            e["object_id"].split(":")[-1]: e for e in json.loads(path.read_text())["elements"]
        }
        source = elements.get("source_structure", {}).get("attributes") or {}
        target = elements.get("target_structure", {})
        if not source.get("method") or target.get("state") != "PRESENT":
            continue
        attributes = target["attributes"]
        assert attributes["match_kind"] is not None, path
        if attributes["match_kind"] == "signature":
            assert attributes["method"] == source["method"], path


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
    """With structure, compatibility, and verification recovered, SAPs reach High.

    Partitioned by whether a grammar is installed for the language, not by file
    extension: Scala is a supported language now, and a SAP that reaches High
    does so because its evidence was recovered, not because of what it is called.
    A language with no grammar keeps its enrichment categories UNAVAILABLE and
    caps lower -- an explicit gap, not a silent skip.
    """
    profiles = sorted(output.glob("*/PR-*/sap-*/characterization.json"))
    assert profiles

    levels = {"LOW": 0, "MODERATE": 1, "HIGH": 2}
    parseable, unparseable = [], []
    for path in profiles:
        profile = json.loads(path.read_text())
        manifest = json.loads((path.parent / "sap.json").read_text())
        ext = manifest["source_file"].rsplit(".", 1)[-1]
        target = parseable if grammar_for(ext) is not None else unparseable
        target.append((path.parent.name, profile))

    assert parseable, "the sample should contain SAPs in a supported language"
    for name, profile in parseable:
        assert profile["aggregate"]["readiness"] == "HIGH", name
        for hunk_id, hunk in profile["hunks"].items():
            assert hunk["applied_constraints"] == [], f"{name}/{hunk_id}"
            assert hunk["coverage_score"] > 0.8, f"{name}/{hunk_id}"

    for name, profile in unparseable:
        # unrecoverable enrichment must lower Readiness, not be ignored
        assert levels[profile["aggregate"]["readiness"]] < levels["HIGH"], name


def test_scala_is_parsed_by_its_own_grammar(output):
    """The Scala SAPs must carry real structural evidence, not an explicit gap.

    Before the Scala grammar was configured these reported UNAVAILABLE for every
    enrichment category and capped below High. Their structure now has to name a
    Scala construct -- a `def`, which Java has no syntax for -- so a regression to
    parsing them as Java could not pass.
    """
    scala = [
        p for p in sorted(output.glob("*/PR-*/sap-*"))
        if json.loads((p / "sap.json").read_text())["source_file"].endswith(".scala")
    ]
    if not scala:
        pytest.skip("no Scala file in the sample")

    named = 0
    for sap_dir in scala:
        for path in sorted(sap_dir.glob("functions/*/structure.json")):
            elements = {
                e["object_id"].split(":")[-1]: e
                for e in json.loads(path.read_text())["elements"]
            }
            source = elements["source_structure"]
            assert source["state"] == "PRESENT", path
            method = (source["attributes"] or {}).get("method")
            if method:
                assert "def " in method, f"{path}: {method!r} is not a Scala definition"
                named += 1
    assert named, "no Scala method signature was recovered"


def test_foundational_categories_are_complete_everywhere(output):
    """Java SAPs must carry complete foundational evidence.

    tau needs a grammar to slice a function out of a file, so a language with no
    tree-sitter grammar configured -- Scala, here -- leaves
    ``function_transformation`` short. That is a real shortfall and stays
    UNAVAILABLE rather than being excused, so the assertion is scoped to the
    languages the pipeline can actually parse.
    """
    for path in sorted(output.glob("*/PR-*/sap-*/characterization.json")):
        manifest = json.loads((path.parent / "sap.json").read_text())
        profile = json.loads(path.read_text())
        foundational = ["source_change", "target_localization"]
        if manifest["source_file"].endswith(".java"):
            foundational.append("function_transformation")
        for hunk_id, hunk in profile["hunks"].items():
            for category in foundational:
                assert hunk["category_scores"][category]["coverage"] == 1.0, (
                    f"{path.parent.name}/{hunk_id}/{category}"
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
