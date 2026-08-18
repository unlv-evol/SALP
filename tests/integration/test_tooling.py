"""External analysis tools, exercised against a real clone.

These need what a developer machine has and CI does not: a bare clone under
``data/repos/``, a RefactoringMiner distribution under ``tools/``, and a JVM. Each
test states its own preconditions and skips when they are absent, so an
incomplete environment reports honestly rather than passing vacuously.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from salp.analyzers import tools
from salp.config import Config
from salp.repos import is_commit_present, is_merge_commit, repo_dir

# A pull request whose commits are not fetched by `salp fetch-repos` unless the
# GACPD sample references it, so the fixture is checked against the clone.
COMMITS = Path("tests/data/Apache_Kafka_PR_12289_Commits_Info.json")
REPO = "apache/kafka"


@pytest.fixture
def cfg() -> Config:
    """The default configuration, with its paths made absolute.

    The config states paths relative to the repository root, and one test below
    changes directory to observe what RefactoringMiner writes; resolving here
    keeps that from silently turning into "tool not found".
    """
    config = Config.load(Path("configs/default.yaml"))
    config.paths.repo_cache = config.paths.repo_cache.resolve()
    if config.tools.refactoringminer_jar is not None:
        config.tools.refactoringminer_jar = config.tools.refactoringminer_jar.resolve()
    return config


@pytest.fixture
def kafka(cfg: Config) -> Path:
    """The apache/kafka clone, skipping when it has not been fetched."""
    clone = repo_dir(cfg.paths.repo_cache, REPO)
    if not clone.is_dir():
        pytest.skip(f"no {REPO} clone under {cfg.paths.repo_cache}; run `salp fetch-repos`")
    return clone


@pytest.fixture
def miner(cfg: Config) -> Path:
    """The launcher for *this* platform, or a skip naming what is missing.

    Resolved through ``refactoringminer_launcher()`` rather than read from the
    config directly. The distribution ships both a Unix launcher and a ``.bat``,
    so reading the configured path would find a file that exists on Windows and
    then fail to run it -- the exact breakage the launcher resolution exists to
    prevent.

    A JVM is checked here too. RefactoringMiner installed without Java makes the
    runner return a diagnostic string, which these tests assert against and would
    report as a failure rather than as the environment gap it is.
    """
    launcher = cfg.tools.refactoringminer_launcher()
    if launcher is None or not launcher.is_file():
        pytest.skip("no RefactoringMiner distribution; see tools/README.md")
    if shutil.which("java") is None:
        pytest.skip("no JVM on PATH; RefactoringMiner cannot run")
    return launcher


@pytest.fixture
def fixture_shas(kafka: Path) -> tuple[str, ...]:
    """The PR 12289 commits, skipping when the clone does not contain them.

    `fetch-repos` fetches `refs/pull/<n>/head` only for the pull requests the
    GACPD sample names, so these commits are absent unless 12289 is among them.
    Without this guard the test does not fail on a real disagreement -- both
    commands correctly refuse the work and merely word it differently.
    """
    fixture = Path(__file__).resolve().parents[1] / "data" / COMMITS.name
    shas = tuple(c["sha"] for c in json.loads(fixture.read_text(encoding="utf-8")))
    missing = [s for s in shas if not is_commit_present(s, kafka)]
    if missing:
        pytest.skip(
            f"{len(missing)} of {len(shas)} PR 12289 commits are absent from the clone; "
            "fetch refs/pull/12289/head into data/repos/apache__kafka.git to run this"
        )
    return shas


# --- RefactoringMiner ---------------------------------------------------------
def test_c_and_bc_agree_over_the_same_range(
    cfg: Config, kafka: Path, miner: Path, fixture_shas: tuple[str, ...]
):
    """Per-commit `-c` and range `-bc` must report the same refactorings.

    The per-commit form exists so merge commits can be excluded, which `-bc`
    cannot express. That is only a safe substitution if the two agree on a range
    containing no merges -- this pins that.

    Note what this does *not* cover: PR 12289 contains no merge commits, so
    agreement here says nothing about the exclusion itself. That is
    `test_a_merge_commit_is_excluded_from_the_analysis` below.
    """
    between = tools.run_refactoring_miner(
        miner, kafka, fixture_shas[0], fixture_shas[-1],
        cfg.paths.repo_cache / ".refactoring-cache", cfg.tools.refactoringminer_timeout,
    )
    per_commit = tools.run_refactoring_miner_list(
        miner, kafka, fixture_shas, cfg.tools.refactoringminer_timeout,
    )
    for label, result in (("-bc", between), ("-c", per_commit)):
        assert not isinstance(result, str), f"{label} failed: {result}"

    # Compared through JSON: the two paths build equal-but-not-identical nested
    # structures, and a round-trip normalises tuple/list differences.
    assert json.loads(json.dumps(per_commit)) == json.loads(json.dumps(between))


def test_an_absent_commit_is_refused_without_touching_the_network(
    cfg: Config, kafka: Path, miner: Path, tmp_path: Path, monkeypatch
):
    """`-c` must never be reached for a commit the clone does not contain.

    Given a commit it cannot find, RefactoringMiner downloads
    `https://github.com/<owner>/<repo>/archive/<sha>.zip` for that commit and its
    parent, unzips each into `<cwd>/<project>-<sha>/`, and exits 0 reporting no
    refactorings. That puts a network fetch inside `salp run` -- which is
    specified to be local and deterministic -- and reports the empty result as
    evidence. `is_commit_present` is the guard that prevents it, and this test
    exists so the guard is not removed as a redundant pre-flight check.

    Runs in an empty working directory, since the archives land relative to it.
    """
    absent = "0" * 40
    assert not is_commit_present(absent, kafka)

    monkeypatch.chdir(tmp_path)
    result = tools.run_refactoring_miner_list(
        miner, kafka, (absent,), cfg.tools.refactoringminer_timeout,
    )

    assert isinstance(result, str), "an absent commit must yield a diagnostic, not a result"
    assert absent in result and "fetch-repos" in result
    assert list(tmp_path.iterdir()) == [], "RefactoringMiner downloaded an archive"


def test_a_merge_commit_is_excluded_from_the_analysis(cfg: Config, kafka: Path, miner: Path):
    """The reason the per-commit form exists, exercised directly.

    A merge's diff attributes every reconciled change to the merge itself, so
    counting it would credit a landing site with refactorings no one performed.
    A list containing only merges must therefore analyse nothing -- and must say
    so as an empty result, not as a diagnostic: the commits were examined and
    deliberately excluded.
    """
    merges = subprocess.run(
        ["git", "-C", str(kafka), "rev-list", "--merges", "-2", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if not merges:
        pytest.skip("no merge commit reachable in the clone")

    for sha in merges:
        assert is_merge_commit(sha, kafka), sha

    result = tools.run_refactoring_miner_list(
        miner, kafka, tuple(merges), cfg.tools.refactoringminer_timeout,
    )
    assert result == (), f"merge commits must be skipped, got {result!r}"


def test_an_empty_commit_list_is_an_empty_result(cfg: Config, kafka: Path, miner: Path):
    """Nothing to analyse is not a failure to analyse."""
    assert tools.run_refactoring_miner_list(miner, kafka, (), 5) == ()
