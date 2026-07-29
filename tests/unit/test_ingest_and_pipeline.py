"""Ingestion, physical layout, and end-to-end pipeline tests.

The fixture mirrors real GACPD output: the record labels it actually emits, CRLF
line endings, hunk artifacts that lead with their unified-diff header, and a
similarity check reported per block kind and per token threshold.
"""

from __future__ import annotations

import json
from pathlib import Path

from salp.config import Config
from salp.ingest import discover_pull_requests
from salp.pipeline import run

SRC_PATH = "streams/src/main/java/org/apache/kafka/streams/kstream/internals/CombinedKey.java"

# GACPD writes CRLF and leaves unset fields blank.
PR_RESULTS = "\r\n".join([
    "Classified PR: 12535",
    "PR Title: KAFKA-13769 Fix version check in SubscriptionStoreReceiveProcessorSupplier",
    "PR Description: This patch fixes another incorrect version check.",
    "PR Location: https://github.com/apache/kafka/pull/12535",
    "REPO DIVERGENCE DATE: 2022-06-02T00:00:00Z",
    "CUTOFF DATE: 2022-12-02T23:59:59Z",
    "",
    "Added Files (Skipped):",
    "Renamed Files:",
    "Files in PR: CombinedKey.java",
    f"Similarity analysis for:  {SRC_PATH}",
    "  - Overall Classification is: MO",
    "",
    "Recommendations: TBO",
])

# The deletion block matches only at the coarsest threshold: a genuine but weak
# alignment, which must read as reduced confidence rather than a failure.
SIMILARITY = "\r\n".join([
    "src/hunk_1_additions.java (50) - has a similarity of: 0%",
    "src/hunk_1_deletions.java (50) - has a similarity of: 0%",
    "src/hunk_1_additions.java (40) - has a similarity of: 0%",
    "src/hunk_1_deletions.java (40) - has a similarity of: 0%",
    "src/hunk_1_additions.java (30) - has a similarity of: 0%",
    "src/hunk_1_deletions.java (30) - has a similarity of: 100%",
])

RESULTS = "\r\n".join([
    "In PR: 12535",
    "Mainline is: apache/kafka",
    "Divergent Repo is: linkedin/kafka",
    f"File: {SRC_PATH}",
    f"Is called in Divergent Path is: Results/Repos_files/run_1/linkedin/kafka/{SRC_PATH}",
    "Similarity Check:",
    SIMILARITY,
    "Classification: ",
    "The final classification is: MO",
])

# Every hunk sits in close(), so they share one function-pool entry.
_SECTION = "public void close() throws Exception {"


def _hunk_header(n: int) -> str:
    return f"@@ -{500 + n * 10},6 +{500 + n * 10},8 @@ {_SECTION}"


def _make_fixture(root: Path, *, hunks: int = 1) -> Path:
    pr_dir = root / "apache_kafka-linked_kafka" / "12535_MO"
    fdir = pr_dir / "MO" / SRC_PATH.replace("/", "_").replace(".", "_")
    (fdir / "src").mkdir(parents=True)
    (fdir / "cmp").mkdir(parents=True)
    (pr_dir / "pr_results.txt").write_text(PR_RESULTS)
    (fdir / "results.txt").write_text(RESULTS)
    (fdir / "cmp" / "CombinedKey.java").write_text("class CombinedKey { }\n")

    patch = ["--- a/" + SRC_PATH, "+++ b/" + SRC_PATH]
    for n in range(1, hunks + 1):
        header = _hunk_header(n)
        patch += [header, f"     keep{n}();", f"+    added{n}();"]
        (fdir / "src" / f"hunk_{n}_full_del.java").write_text(f"{header}\n     keep{n}();\n")
        (fdir / "src" / f"hunk_{n}_full_add.java").write_text(
            f"{header}\n     keep{n}();\n     added{n}();\n"
        )
    (fdir / "src" / "CombinedKey.patch").write_text("\n".join(patch) + "\n")

    na = pr_dir / "NA" / "streams_src_main_java_SubscriptionWrapper_java"
    (na / "src").mkdir(parents=True)
    (na / "results.txt").write_text(
        "In PR: 12535\r\nMainline is: apache/kafka\r\nDivergent Repo is: linkedin/kafka\r\n"
        "File: streams/src/main/java/SubscriptionWrapper.java\r\n"
        "The final classification is: NA\r\n"
    )
    (na / "src" / "SubscriptionWrapper.java").write_text("class SubscriptionWrapper {}\n")
    return pr_dir


def _run(tmp_path: Path, **kwargs: int) -> Path:
    _make_fixture(tmp_path, **kwargs)
    cfg = Config()
    cfg.paths.gacpd_run = tmp_path
    cfg.paths.output = tmp_path / "out"
    assert run(cfg) == 1
    # output is grouped by variant pair, target-first
    return tmp_path / "out" / "linkedinKafka-apacheKafka" / "PR-12535"


# --- ingestion ---------------------------------------------------------------
def test_discovery_finds_mo_file_and_retains_na_sibling(tmp_path: Path):
    _make_fixture(tmp_path)
    prs = discover_pull_requests(tmp_path)
    assert len(prs) == 1
    pr = prs[0]
    assert len(pr.mo_files) == 1
    assert len(pr.context_files) == 1
    assert pr.mo_files[0].hunks[0].hunk_id == "H-1"


def test_ingest_recovers_extension_and_real_file_name(tmp_path: Path):
    _make_fixture(tmp_path)
    mo = discover_pull_requests(tmp_path)[0].mo_files[0]
    # the directory name is the flattened source path; the real name comes from results.txt
    assert mo.ext == "java"
    assert mo.display_name == "CombinedKey.java"
    assert mo.source_path == SRC_PATH


def test_ingest_parses_pr_metadata(tmp_path: Path):
    _make_fixture(tmp_path)
    m = discover_pull_requests(tmp_path)[0].metadata
    assert m.number == "12535"
    assert m.title.startswith("KAFKA-13769")
    assert m.url == "https://github.com/apache/kafka/pull/12535"
    # timestamps are reduced to the calendar day
    assert (m.divergence_date, m.cutoff_date) == ("2022-06-02", "2022-12-02")
    # the pair is promoted from the per-file records, not the run directory,
    # which abbreviates the divergent repository as "linked_kafka"
    assert (m.source_repo, m.target_repo) == ("apache/kafka", "linkedin/kafka")


def test_blank_field_stays_empty_instead_of_swallowing_the_next_line(tmp_path: Path):
    pr_dir = _make_fixture(tmp_path)
    (pr_dir / "pr_results.txt").write_text(
        "Classified PR: 2731\r\nPR Title: \r\nPR Description: \r\nPR Location: \r\n"
        "REPO DIVERGENCE DATE: 2022-09-08T00:00:00Z\r\nCUTOFF DATE: 2023-03-08T23:59:59Z\r\n"
    )
    m = discover_pull_requests(tmp_path)[0].metadata
    assert m.title is None and m.url is None
    assert m.divergence_date == "2022-09-08"
    assert any("no title" in d for d in m.diagnostics)


def test_localization_strips_the_gacpd_working_directory_prefix(tmp_path: Path):
    _make_fixture(tmp_path)
    loc = discover_pull_requests(tmp_path)[0].mo_files[0].localization
    assert loc.divergent_path == SRC_PATH
    assert loc.divergent_path_raw.startswith("Results/Repos_files/")


def test_alignment_confidence_averages_the_deletion_anchor_over_thresholds(tmp_path: Path):
    """A block matching only at the coarsest threshold is reduced confidence."""
    _make_fixture(tmp_path)
    loc = discover_pull_requests(tmp_path)[0].mo_files[0].localization
    assert loc.confidence("H-1") == 1 / 3
    assert loc.breakdown("H-1") == {
        "additions": {50: 0.0, 40: 0.0, 30: 0.0},
        "deletions": {50: 0.0, 40: 0.0, 30: 1.0},
    }


# --- physical layout ---------------------------------------------------------
def test_layout_matches_canonical_directory_grammar(tmp_path: Path):
    pr_dir = _run(tmp_path)
    sap_dir = pr_dir / "sap-CombinedKey"
    fn = "functions/CombinedKey_close"
    for rel in (
        "sap.json", "characterization.json", "provenance.json", "change.json",
        f"{fn}/source.before.java", f"{fn}/source.after.java", f"{fn}/target.java",
        f"{fn}/structure.json",
        "hunks/H-1/hunk.json", "hunks/H-1/hunk.diff", "hunks/H-1/edit_region.json",
        "hunks/H-1/transformation.json", "hunks/H-1/localization.json",
        "hunks/H-1/refactorings.json", "hunks/H-1/compatibility.json",
        "hunks/H-1/surrounding.json", "hunks/H-1/verification.json",
        "hunks/H-1/provenance.json",
    ):
        assert (sap_dir / rel).is_file(), f"missing {rel}"
    assert (pr_dir / "pr.json").is_file()


def test_payloads_are_raw_source_files_not_escaped_json(tmp_path: Path):
    sap_dir = _run(tmp_path) / "sap-CombinedKey"
    target = sap_dir / "functions/CombinedKey_close/target.java"
    assert target.read_text() == "class CombinedKey { }\n"
    assert (sap_dir / "hunks/H-1/hunk.diff").read_text().startswith("--- a/streams")


def test_index_files_carry_no_program_text(tmp_path: Path):
    pr_dir = _run(tmp_path)
    for index in (pr_dir / "pr.json", pr_dir / "sap-CombinedKey" / "sap.json"):
        assert "payloads" not in json.loads(index.read_text())


def test_na_sibling_is_retained_as_context_not_minted(tmp_path: Path):
    pr_dir = _run(tmp_path)
    manifest = json.loads((pr_dir / "pr.json").read_text())
    assert [s["sap_id"] for s in manifest["saps"]] == ["RC-12535-CombinedKey"]
    (entry,) = manifest["context_files"]
    assert entry["gacpd_classification"] == "NA"
    # every referenced context file must exist under _context/
    assert (pr_dir / entry["path"]).is_file()
    assert not (pr_dir / "sap-SubscriptionWrapper").exists()


def test_every_index_payload_reference_resolves(tmp_path: Path):
    sap_dir = _run(tmp_path, hunks=2) / "sap-CombinedKey"
    for hunk_id in ("H-1", "H-2"):
        index = json.loads((sap_dir / "hunks" / hunk_id / "hunk.json").read_text())
        refs = [v for v in index["transformation"].values() if isinstance(v, str)]
        refs += [e["ref"] for e in index["evidence"].values() if e.get("ref")]
        for ref in refs:
            resolved = sap_dir / ref
            if not resolved.exists():  # hunk-relative evidence documents
                resolved = sap_dir / "hunks" / hunk_id / ref
            assert resolved.is_file(), f"dangling reference {ref} in {hunk_id}"


# --- function pool ------------------------------------------------------------
def test_hunks_in_the_same_function_share_one_pool_entry(tmp_path: Path):
    """f_s, f'_s, and f_t are stored once per function, not once per hunk."""
    sap_dir = _run(tmp_path, hunks=3) / "sap-CombinedKey"
    assert [p.name for p in (sap_dir / "functions").iterdir()] == ["CombinedKey_close"]
    for hunk_id in ("H-1", "H-2", "H-3"):
        index = json.loads((sap_dir / "hunks" / hunk_id / "hunk.json").read_text())
        assert index["transformation"]["f_s_before"] == (
            "functions/CombinedKey_close/source.before.java"
        )


def test_each_hunk_gets_its_own_slice_of_the_patch(tmp_path: Path):
    sap_dir = _run(tmp_path, hunks=3) / "sap-CombinedKey"
    diffs = {h: (sap_dir / "hunks" / h / "hunk.diff").read_text() for h in ("H-1", "H-2", "H-3")}
    assert len(set(diffs.values())) == 3, "hunks must not share one whole-file patch"
    for n, hunk_id in enumerate(("H-1", "H-2", "H-3"), start=1):
        assert f"added{n}();" in diffs[hunk_id]
        # each slice keeps the file header, so it stays a self-contained diff
        assert diffs[hunk_id].startswith("--- a/streams")


def test_edit_region_records_real_spans_from_the_diff_header(tmp_path: Path):
    sap_dir = _run(tmp_path) / "sap-CombinedKey"
    doc = json.loads((sap_dir / "hunks" / "H-1" / "edit_region.json").read_text())
    # transformation.json carries the edit-region element
    doc = json.loads((sap_dir / "hunks" / "H-1" / "transformation.json").read_text())
    spans = next(e for e in doc["elements"] if e["element"].endswith("edit_regions"))
    assert spans["attributes"]["spans"]["source_before"]["start"] == 510
    assert spans["attributes"]["spans"]["section"] == _SECTION


# --- characterization over the real pipeline ---------------------------------
def test_gacpd_only_package_characterizes_moderate(tmp_path: Path):
    """A GACPD-only package has every foundational category PRESENT.

    Its Coverage is reduced by the UNAVAILABLE SALP-enriched categories, so it
    characterizes as Moderate at best -- enrichment is what raises Coverage and,
    with it, Readiness.
    """
    sap_dir = _run(tmp_path) / "sap-CombinedKey"
    profile = json.loads((sap_dir / "characterization.json").read_text())
    assert profile["aggregate"]["readiness"] == "MODERATE"

    hunk = profile["hunks"]["H-1"]
    assert hunk["applied_constraints"] == []  # no foundational element is UNAVAILABLE
    for foundational in ("source_change", "target_localization", "function_transformation"):
        assert hunk["category_scores"][foundational]["coverage"] == 1.0
    # unresolved enrichment is what holds Coverage down
    assert 0 < hunk["coverage_score"] < 1.0
    assert hunk["category_scores"]["refactoring"]["coverage"] == 0.0


def test_levels_serialize_as_names(tmp_path: Path):
    sap_dir = _run(tmp_path) / "sap-CombinedKey"
    hunk = json.loads((sap_dir / "characterization.json").read_text())["hunks"]["H-1"]
    assert hunk["readiness_final"] in {"LOW", "MODERATE", "HIGH"}
    assert isinstance(hunk["coverage_level"], str)


def test_composite_sap_readiness_is_minimum_over_hunks(tmp_path: Path):
    sap_dir = _run(tmp_path, hunks=3) / "sap-CombinedKey"
    profile = json.loads((sap_dir / "characterization.json").read_text())
    assert profile["aggregate"]["hunk_count"] == 3
    assert profile["aggregate"]["determined_by_hunk"] in {"H-1", "H-2", "H-3"}
    levels = {"LOW": 0, "MODERATE": 1, "HIGH": 2}
    per_hunk = [levels[h["readiness_final"]] for h in profile["hunks"].values()]
    assert levels[profile["aggregate"]["readiness"]] == min(per_hunk)
