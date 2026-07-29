"""Repository-state binding: resolving GACPD dates to concrete commits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from salp.config import get_logger
from salp.models import RepositoryStatePin
from salp.repos.cache import PR_REF, is_cloned, is_slug, repo_dir
from salp.repos.git import run

log = get_logger(__name__)



@dataclass
class PinResolver:
    """Resolves repository-state pins against the local bare-clone """

    cache_dir: Path
    enabled: bool = True

    def _unresolved(self, repo: str | None, reason: str) -> RepositoryStatePin | None:
        if not repo:
            return None
        return RepositoryStatePin(repo=repo, resolved_from=None, diagnostics=reason)

    def _preflight(self, repo: str | None) -> str | None:
        """The reason resolution cannot proceed, or None when it can."""
        if not self.enabled:
            return "repository resolution disabled"
        if not is_slug(repo):
            return f"repository {repo!r} is not an owner/name slug"
        assert repo is not None
        if not is_cloned(self.cache_dir, repo):
            return (
                f"no local clone of {repo}; run `salp fetch-repos` to bind this SAP "
                "to a commit"
            )
        return None

    def source(self, repo: str | None, pr_number: str | None) -> RepositoryStatePin | None:
        """Pin the source repository to the pull request's head commit."""
        if (reason := self._preflight(repo)) is not None:
            return self._unresolved(repo, reason)
        assert repo is not None
        if not pr_number:
            return self._unresolved(repo, "no pull-request number to resolve")

        ref = f"{PR_REF}/{pr_number}"
        result = run(
            "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
            cwd=repo_dir(self.cache_dir, repo),
        )
        if not result.ok or not result.text:
            return self._unresolved(
                repo,
                f"{ref} not fetched; run `salp fetch-repos` to fetch pull-request heads",
            )
        return RepositoryStatePin(repo=repo, commit=result.text, resolved_from=ref)

    def target(self, repo: str | None, cutoff: str | None) -> RepositoryStatePin | None:
        """Pin the target repository to the last commit at or before the cutoff."""
        if (reason := self._preflight(repo)) is not None:
            return self._unresolved(repo, reason)
        assert repo is not None
        if not cutoff:
            return self._unresolved(repo, "no cutoff date to resolve")

        result = run(
            "rev-list", "-1", f"--before={cutoff}", "HEAD",
            cwd=repo_dir(self.cache_dir, repo),
        )
        if not result.ok or not result.text:
            return self._unresolved(
                repo, f"no commit on the default branch at or before {cutoff}"
            )
        return RepositoryStatePin(
            repo=repo, commit=result.text, resolved_from=f"HEAD before {cutoff}"
        )
