"""Local repository cache: bare clones, state pins, and pinned-state access.

Every repository operation is a git operation, so nothing here calls the GitHub
REST API. Fetching is exposed as ``salp fetch-repos``; everything the pipeline
does against the cache is local, keeping a run deterministic.
"""

from salp.repos.cache import (
    DEFAULT_REMOTE,
    PR_REF,
    CloneOutcome,
    clone,
    fetch_default,
    fetch_pull_request,
    has_pull_request_ref,
    is_cloned,
    is_slug,
    repo_dir,
    repo_dirname,
)
from salp.repos.files import (
    file_exists,
    find_build_files,
    grep_files,
    list_tree,
    read_dependencies,
    read_file,
)
from salp.repos.git import (
    GitResult,
    git_available,
)
from salp.repos.pins import (
    PinResolver,
)

__all__ = [
    "CloneOutcome",
    "DEFAULT_REMOTE",
    "GitResult",
    "PR_REF",
    "PinResolver",
    "clone",
    "fetch_default",
    "fetch_pull_request",
    "file_exists",
    "find_build_files",
    "git_available",
    "grep_files",
    "has_pull_request_ref",
    "is_cloned",
    "is_slug",
    "list_tree",
    "read_dependencies",
    "read_file",
    "repo_dir",
    "repo_dirname",
]
