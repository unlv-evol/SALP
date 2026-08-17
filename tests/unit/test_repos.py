"""Repository cache, state pins, and pinned-state file access.

These run against real local git repositories rather than mocks, so the actual
plumbing -- bare cloning, pull-request refs, date-based resolution, reading a
blob without a working tree -- is exercised. No network is involved: the
"remote" is a directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from salp.models import RepositoryStatePin
from salp.repos import (
    PinResolver,
    clone,
    fetch_pull_request,
    file_exists,
    git_available,
    has_pull_request_ref,
    is_cloned,
    is_commit_present,
    is_merge_commit,
    list_tree,
    read_file,
    repo_dir,
)

pytestmark = pytest.mark.skipif(not git_available(), reason="git is not installed")

SLUG = "acme/widget"


def _git(cwd: Path, *args: str, when: str | None = None) -> None:
    env = {
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A local repository standing in for the GitHub remote."""
    origin = tmp_path / "remote" / f"{SLUG}.git"
    work = tmp_path / "work"
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")

    (work / "src.java").write_text("class A { void old() {} }\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "before cutoff", when="2022-01-01T00:00:00Z")

    (work / "src.java").write_text("class A { void renamed() {} }\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "after cutoff", when="2023-01-01T00:00:00Z")

    origin.parent.mkdir(parents=True, exist_ok=True)
    _git(work, "clone", "-q", "--bare", str(work), str(origin))
    return tmp_path / "remote"


@pytest.fixture
def cache_dir(tmp_path: Path, remote: Path) -> Path:
    cdir = tmp_path / "repos"
    outcome = clone(cdir, SLUG, remote_base=f"{remote}/")
    assert outcome.ok and outcome.cloned
    return cdir


# --- the cache ----------------------------------------------------------------
def test_clone_is_bare_and_has_no_working_tree(cache_dir: Path):
    directory = repo_dir(cache_dir, SLUG)
    assert directory.name == "acme__widget.git"
    assert (directory / "HEAD").is_file()
    assert not (directory / "src.java").exists(), "a bare clone must have no checkout"
    assert is_cloned(cache_dir, SLUG)


def test_cloning_twice_is_a_no_op(cache_dir: Path, remote: Path):
    again = clone(cache_dir, SLUG, remote_base=f"{remote}/")
    assert again.ok and not again.cloned


def test_a_malformed_slug_is_reported_not_raised(tmp_path: Path):
    outcome = clone(tmp_path / "repos", "not-a-slug")
    assert not outcome.ok and "slug" in outcome.error


# --- pin resolution -----------------------------------------------------------
def test_cutoff_date_resolves_to_the_last_commit_before_it(cache_dir: Path):
    pin = PinResolver(cache_dir).target(SLUG, "2022-06-01T00:00:00Z")
    assert pin.is_resolved
    assert "before" in pin.resolved_from
    # the 2023 commit is past the cutoff and must not be selected
    content = read_file(cache_dir, pin, "src.java")
    assert "old()" in content and "renamed()" not in content


def test_a_cutoff_before_all_history_resolves_to_nothing(cache_dir: Path):
    pin = PinResolver(cache_dir).target(SLUG, "2001-01-01T00:00:00Z")
    assert not pin.is_resolved
    assert "at or before" in pin.diagnostics


def test_pull_request_head_resolves_once_fetched(cache_dir: Path, remote: Path):
    # stand in for refs/pull/7/head, which GitHub serves over the git protocol
    origin = remote / f"{SLUG}.git"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(origin, "update-ref", "refs/pull/7/head", head)

    resolver = PinResolver(cache_dir)
    assert not resolver.source(SLUG, "7").is_resolved  # not fetched yet

    assert fetch_pull_request(cache_dir, SLUG, "7").ok
    assert has_pull_request_ref(cache_dir, SLUG, "7")
    pin = resolver.source(SLUG, "7")
    assert pin.commit == head
    assert pin.resolved_from == "refs/salp/pr/7"


def test_missing_clone_degrades_to_an_unresolved_pin_with_guidance(tmp_path: Path):
    pin = PinResolver(tmp_path / "empty").target("acme/absent", "2022-06-01T00:00:00Z")
    assert not pin.is_resolved
    assert "salp fetch-repos" in pin.diagnostics


def test_resolution_can_be_disabled(cache_dir: Path):
    pin = PinResolver(cache_dir, enabled=False).target(SLUG, "2022-06-01T00:00:00Z")
    assert not pin.is_resolved
    assert "disabled" in pin.diagnostics


def test_an_unknown_repository_never_raises(cache_dir: Path):
    assert PinResolver(cache_dir).source(None, "7") is None
    assert PinResolver(cache_dir).target("garbage", "2022-01-01").is_resolved is False


# --- pinned-state file access -------------------------------------------------
def test_files_are_read_without_a_checkout(cache_dir: Path):
    pin = PinResolver(cache_dir).target(SLUG, "2024-01-01T00:00:00Z")
    assert read_file(cache_dir, pin, "src.java").startswith("class A")
    assert list_tree(cache_dir, pin) == ["src.java"]
    assert file_exists(cache_dir, pin, "src.java")
    assert not file_exists(cache_dir, pin, "nope.java")


def test_reading_against_an_unresolved_pin_returns_nothing(cache_dir: Path):
    unresolved = RepositoryStatePin(repo=SLUG)
    assert read_file(cache_dir, unresolved, "src.java") is None
    assert list_tree(cache_dir, unresolved) == []
    assert read_file(cache_dir, None, "src.java") is None


# --- commit-level predicates -------------------------------------------------
@pytest.fixture
def branched_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A repository with an ordinary commit, a branch commit, and a merge.

    Built here rather than resolved against SALP's own history: CI checks out at
    depth 1, so a hardcoded SHA is simply absent there, and any SHA would break
    the moment the branch is rebased. Identity is set per-invocation, since a
    runner has no global git identity.
    """
    repo = tmp_path / "branched"
    repo.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-c", "user.name=SALP Test", "-c", "user.email=test@salp.invalid",
             "-c", "commit.gpgsign=false", *args],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    git("init", "-q", "--initial-branch=main")

    (repo / "file1.txt").write_text("initial\n")
    git("add", "file1.txt")
    git("commit", "-q", "-m", "initial commit")
    initial = git("rev-parse", "HEAD")

    git("checkout", "-q", "-b", "feature")
    (repo / "file2.txt").write_text("feature\n")
    git("add", "file2.txt")
    git("commit", "-q", "-m", "feature commit")
    feature = git("rev-parse", "HEAD")

    git("checkout", "-q", "main")
    git("merge", "-q", "--no-ff", "feature", "-m", "merge feature")
    merge = git("rev-parse", "HEAD")

    return repo, initial, feature, merge


def test_a_merge_commit_is_distinguished_from_an_ordinary_one(branched_repo):
    repo, initial, feature, merge = branched_repo
    assert is_merge_commit(merge, repo) is True
    assert is_merge_commit(initial, repo) is False
    assert is_merge_commit(feature, repo) is False


def test_a_commit_that_is_not_there_is_neither_present_nor_a_merge(branched_repo):
    """An absent commit must not read as a merge, and must be reported absent.

    Callers use `is_commit_present` to keep analysis off the network, so a wrong
    answer here has consequences beyond this predicate.
    """
    repo, *_ = branched_repo
    assert is_commit_present("0" * 40, repo) is False
    assert is_merge_commit("0" * 40, repo) is False


def test_present_rejects_an_object_that_is_not_a_commit(branched_repo):
    """A hexadecimal string can name a blob; a commit-level analysis cannot use one."""
    repo, initial, _, _ = branched_repo
    blob = subprocess.run(
        ["git", "rev-parse", f"{initial}:file1.txt"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert is_commit_present(initial, repo) is True
    assert is_commit_present(blob, repo) is False


def test_the_predicates_reject_an_empty_sha(branched_repo):
    repo, *_ = branched_repo
    assert is_commit_present("", repo) is False
    assert is_merge_commit("", repo) is False
