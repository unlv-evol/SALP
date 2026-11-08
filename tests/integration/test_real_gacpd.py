"""End-to-end checks against a real GACPD sample, trimmed to what SALP reads.

The sample under ``tests/data/gacpd_sample`` is real GACPD output for two pull
requests, plus the few repository files the pipeline reads at each pinned state.
Those files are committed into throwaway bare repositories so that pins resolve
and files are read exactly as against a full clone. The tests guard the parsers
against the formats GACPD actually emits -- CRLF endings, per-threshold
similarity, one-sided hunks -- which a synthetic fixture can only approximate.

See ``tests/data/gacpd_sample/README.md`` for what was kept and how.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from salp.config import Config
from salp.ingest import discover_pull_requests
from salp.packaging import validate_sap
from salp.pipeline import run
from salp.repos.cache import PR_REF
from salp.structural import TREE_SITTER_AVAILABLE, grammar_for

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "gacpd_sample"
DATA = SAMPLE / "gacpd"
REPOS = SAMPLE / "repos"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
]

# For what only a parser can recover: methods, classes, grammar-level structure.
# Everything else here runs without the `structural` extra, exercising the
# degraded path SALP supports.
requires_parser = pytest.mark.skipif(
    not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed"
)

# Before every cutoff and divergence date in the sample, so each target pin
# resolves to the one snapshot commit.
_SNAPSHOT_DATE = "2000-01-01T00:00:00Z"


def _git(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        check=True, capture_output=True, env={**os.environ, **(env or {})},
    )


def _build_repo_cache(cache: Path, scratch: Path) -> None:
    """One bare repository per snapshot, with each source pull request's head ref."""
    pull_requests = json.loads((REPOS / "pull_requests.json").read_text())
    identity = {
        "GIT_AUTHOR_NAME": "salp-tests", "GIT_AUTHOR_EMAIL": "salp-tests@example.invalid",
        "GIT_COMMITTER_NAME": "salp-tests", "GIT_COMMITTER_EMAIL": "salp-tests@example.invalid",
        "GIT_AUTHOR_DATE": _SNAPSHOT_DATE, "GIT_COMMITTER_DATE": _SNAPSHOT_DATE,
    }
    for snapshot in sorted(p for p in REPOS.iterdir() if p.is_dir()):
        bare = cache / f"{snapshot.name}.git"
        tree = ["--git-dir", str(bare), "--work-tree", str(snapshot)]
        env = {**identity, "GIT_INDEX_FILE": str(scratch / f"{snapshot.name}.index")}
        _git("init", "--quiet", "--bare", str(bare))
        _git(*tree, "add", "--all", env=env)
        _git(*tree, "commit", "--quiet", "--message", "snapshot", env=env)
        for number in pull_requests.get(snapshot.name, []):
            _git("--git-dir", str(bare), "update-ref", f"{PR_REF}/{number}", "HEAD")


@pytest.fixture(scope="module")
def pull_requests():
    return {pr.metadata.number: pr for pr in discover_pull_requests(DATA)}


@pytest.fixture(scope="module")
def output(tmp_path_factory) -> Path:
    cache = tmp_path_factory.mktemp("repo-cache")
    _build_repo_cache(cache, tmp_path_factory.mktemp("index"))
    out = tmp_path_factory.mktemp("real-run")
    cfg = Config()
    cfg.paths.gacpd_run = DATA
    cfg.paths.output = out
    cfg.paths.repo_cache = cache
    assert run(cfg) > 0
    return out


def _sap_dir(output: Path, file_name: str) -> Path:
    """The SAP minted for a source file, e.g. ``MnemonicCode-java``."""
    (sap_dir,) = output.glob(f"*/PR-*/sap-*-{file_name}")
    return sap_dir


# --- ingestion ----------------------------------------------------------------
def test_every_pull_request_recovers_its_repository_pair(pull_requests):
    """The pair comes from the per-file records, not the abbreviated run directory."""
    for number, pr in pull_requests.items():
        m = pr.metadata
        assert m.source_repo and m.target_repo, f"PR {number} recovered no repository pair"
        assert "/" in m.source_repo and "/" in m.target_repo
        assert m.divergence_date and m.cutoff_date, f"PR {number} has no dates"


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
    """MnemonicCodeTest H-2's deletion block matches only at the coarser threshold."""
    (mo,) = [m for m in pull_requests["2785"].mo_files if m.display_name == "MnemonicCodeTest.java"]
    assert mo.localization.confidence("H-2") == pytest.approx(1 / 2)
    breakdown = mo.localization.breakdown("H-2")
    assert breakdown["deletions"] == {50: 0.0, 40: 1.0}
    assert breakdown["additions"] == {50: 0.0, 40: 0.0}


def test_a_pure_insertion_is_aligned_by_its_additions(pull_requests):
    """MnemonicCode H-1 deletes nothing, so it has no deletion anchor to score."""
    (mo,) = [m for m in pull_requests["2785"].mo_files if m.display_name == "MnemonicCode.java"]
    assert mo.localization.breakdown("H-1")["deletions"] == {}
    assert mo.localization.confidence("H-1") == pytest.approx(0.571429 / 4)


# --- construction -------------------------------------------------------------
def test_pure_insertion_hunk_recovers_its_pre_change_side(output):
    """MnemonicCode H-1 only adds, so GACPD emits no full_del; the diff supplies it."""
    sap_dir = _sap_dir(output, "MnemonicCode-java")
    doc = json.loads((sap_dir / "hunks" / "H-1" / "edit_region.json").read_text())
    pre = next(e for e in doc["elements"] if e["element"].endswith("pre_change_function"))
    assert pre["state"] == "PRESENT", "the omitted side must be reconstructed from the diff"
    assert (sap_dir / pre["attributes"]["payload_ref"]).is_file()


@requires_parser
def test_an_import_region_edit_has_no_function_transformation(output):
    """MnemonicCode H-1 edits the import block, which has no enclosing function.

    tau is undefined there rather than unrecovered, so it must be NOT_APPLICABLE
    -- which leaves both denominators -- and not UNAVAILABLE, which would score
    the hunk down for evidence it structurally cannot have.
    """
    sap_dir = _sap_dir(output, "MnemonicCode-java")
    doc = json.loads((sap_dir / "hunks" / "H-1" / "transformation.json").read_text())
    unit = next(e for e in doc["elements"] if e["element"].endswith("transformation_unit"))
    assert unit["state"] == "NOT_APPLICABLE"
    assert "import block" in unit["provenance"]["diagnostics"]


def test_every_target_payload_occurs_verbatim_in_the_fork_file(output):
    """RCAP replaces f_t by finding its exact text in the fork.

    An import block rebuilt from its import nodes lost the blank lines between
    groups, so it occurred nowhere in the fork and every unit needing it failed.
    """
    checked = 0
    for sap_dir in sorted(output.glob("*/PR-*/sap-*")):
        target_repo = json.loads((sap_dir.parent / "pr.json").read_text())["target_repo"]
        target_file = json.loads((sap_dir / "sap.json").read_text())["target_file"]
        fork = (REPOS / target_repo.replace("/", "__") / target_file).read_text(encoding="utf-8")
        for payload in sorted(sap_dir.glob("functions/*/target.*")):
            text = payload.read_text(encoding="utf-8")
            assert fork.count(text) == 1, payload
            if payload.parent.name.endswith("_imports"):
                assert text.endswith("\n"), f"{payload}: a block is whole lines"
            checked += 1
    assert checked, "no target payload was written"


def test_every_hunk_carries_its_region_as_an_exact_slice_of_the_file(output):
    """Both sides of every hunk, context lines included, as the files hold them.

    RCAP falls back to these when the unit is not a method on both sides, and
    finds where to apply a change by searching for the before side's lines.
    """
    from salp.ingest import hunk_region, parse_hunk_header

    checked = 0
    for index_path in sorted(output.glob("*/PR-*/sap-*/hunks/*/hunk.json")):
        index = json.loads(index_path.read_text())
        sap_dir = index_path.parent.parent.parent
        assert index["region_cut_from"] == "file", index_path
        diff = (index_path.parent / "hunk.diff").read_text(encoding="utf-8")
        header = parse_hunk_header(diff)
        source_repo = json.loads((sap_dir.parent / "pr.json").read_text())["source_repo"]
        source_file = json.loads((sap_dir / "sap.json").read_text())["source_file"]
        head = (REPOS / source_repo.replace("/", "__") / source_file).read_text(encoding="utf-8")
        exact_after = "".join(head.splitlines(keepends=True)[
            header.new_start - 1 : header.new_start - 1 + header.new_count
        ])
        for side, expected in (
            ("before", hunk_region(diff, side="before")), ("after", exact_after)
        ):
            region = (sap_dir / index["transformation"][f"region_{side}"]).read_text(
                encoding="utf-8"
            )
            assert region == expected, f"{index_path}: region_{side}"
            assert region.endswith("\n"), f"{index_path}: region_{side} ends mid-line"
        checked += 1
    assert checked, "no hunk was written"


@requires_parser
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


@requires_parser
def test_structural_evidence_describes_the_pooled_function(output):
    """A function's structure.json must describe the method its payloads hold.

    The structural analyzer once located from the pre-change header span --
    diff context included -- against the post-change file, so a folder holding
    `testBadEntropyLength` carried structure for `testGetWordListUnmodifiable`.
    """
    checked = 0
    for path in sorted(output.glob("*/PR-*/sap-*/hunks/*/transformation.json")):
        doc = json.loads(path.read_text())
        unit = next(e for e in doc["elements"] if e["element"].endswith("transformation_unit"))
        signature = (unit.get("attributes") or {}).get("signature")
        if unit["state"] != "PRESENT" or not signature:
            continue
        sap_dir = path.parent.parent.parent
        fn_dir = json.loads((path.parent / "hunk.json").read_text())["transformation"]["f_s_after"]
        structure = json.loads((sap_dir / fn_dir.rsplit("/", 1)[0] / "structure.json").read_text())
        source = next(
            e for e in structure["elements"] if e["element"].endswith("source_structure")
        )
        assert source["attributes"]["method"] == signature, path
        checked += 1
    assert checked, "no hunk resolved to a pooled function"


@requires_parser
def test_target_structure_describes_the_pooled_target_function(output):
    """structure.json must describe the same target method f_t holds.

    Both are matched from the pre-change source method. The analyzer once
    matched from the post-change one, which a target cannot contain yet when
    the change edits the method's own signature.
    """
    checked = 0
    for path in sorted(output.glob("*/PR-*/sap-*/hunks/*/localization.json")):
        located = next(
            e for e in json.loads(path.read_text())["elements"]
            if e["element"].endswith("localized_target_function")
        )
        pooled = (located.get("attributes") or {}).get("method")
        if not pooled:
            continue
        sap_dir = path.parent.parent.parent
        fn_dir = json.loads((path.parent / "hunk.json").read_text())["transformation"]["f_t"]
        structure = json.loads((sap_dir / fn_dir.rsplit("/", 1)[0] / "structure.json").read_text())
        target = next(
            e for e in structure["elements"] if e["element"].endswith("target_structure")
        )
        assert target["state"] == "PRESENT", path
        assert target["attributes"]["method"] == pooled, path
        checked += 1
    assert checked, "no hunk's target resolved to a pooled function"


def test_the_target_function_is_matched_by_signature_not_line_number(output):
    """f_t must be the counterpart of f_s, not whatever occupies the same lines.

    Reusing the source's line span against a diverged variant resolved `saveNow`
    to `saveNowInternal` and reported it PRESENT.

    An exact match equals the signature it was matched from -- the pre-change
    one -- not the post-change source, which differs whenever the change edits
    the signature (MnemonicCode's toMnemonic drops a throws clause).
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
            assert attributes["method"] == attributes["matched_from"], path


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
@requires_parser
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


@requires_parser
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


@requires_parser
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
