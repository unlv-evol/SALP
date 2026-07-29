"""The local bare-clone cache."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from salp.config import get_logger
from salp.repos.git import GitResult, run, run_network

log = get_logger(__name__)


_SLUG = re.compile(r"^[\w.\-]+/[\w.\-]+$")


# GACPD reports repositories as owner/name; SALP only ever reads public history.
DEFAULT_REMOTE = "https://github.com/"


# Fetched pull-request heads are kept under a SALP-owned namespace so they
# survive later fetches and stay inspectable with `git for-each-ref`.
PR_REF = "refs/salp/pr"


def is_slug(repo: str | None) -> bool:
    return bool(repo and _SLUG.match(repo))


def repo_dirname(slug: str) -> str:
    """``apache/kafka`` -> ``apache__kafka.git``."""
    return f"{slug.replace('/', '__')}.git"


def repo_dir(cache: Path, slug: str) -> Path:
    return cache / repo_dirname(slug)


def is_cloned(cache: Path, slug: str) -> bool:
    directory = repo_dir(cache, slug)
    return directory.is_dir() and (directory / "HEAD").is_file()


@dataclass
class CloneOutcome:
    slug: str
    path: Path
    cloned: bool
    fetched_refs: list[str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def clone(cache: Path, slug: str, *, remote_base: str = DEFAULT_REMOTE) -> CloneOutcome:
    """Clone a repository bare, or report the existing clone."""
    directory = repo_dir(cache, slug)
    if not is_slug(slug):
        return CloneOutcome(slug, directory, False, [], f"not an owner/name slug: {slug!r}")
    if is_cloned(cache, slug):
        return CloneOutcome(slug, directory, False, [])

    cache.mkdir(parents=True, exist_ok=True)
    log.info("cloning %s (bare, full history) into %s", slug, directory)
    result = run_network("clone", "--bare", f"{remote_base}{slug}.git", str(directory))
    if not result.ok:
        return CloneOutcome(slug, directory, False, [], result.stderr.strip() or "clone failed")
    return CloneOutcome(slug, directory, True, [])


def fetch_pull_request(cache: Path, slug: str, number: str) -> GitResult:
    """Fetch a pull request's head into the SALP ref namespace.

    ``refs/pull/<n>/head`` is served over the git wire protocol, so the merge
    commit of a pull request is reachable without a single API call.
    """
    return run_network(
        "fetch", "--no-tags", "--force", "origin",
        f"+refs/pull/{number}/head:{PR_REF}/{number}",
        cwd=repo_dir(cache, slug),
    )


def fetch_default(cache: Path, slug: str) -> GitResult:
    """Refresh the default branch so date-based resolution sees recent history."""
    return run_network("fetch", "--no-tags", "--force", "origin", cwd=repo_dir(cache, slug))


def has_pull_request_ref(cache: Path, slug: str, number: str) -> bool:
    result = run(
        "rev-parse", "--verify", "--quiet", f"{PR_REF}/{number}", cwd=repo_dir(cache, slug)
    )
    return result.ok and bool(result.text)
