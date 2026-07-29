"""GACPD output ingestion (MO scope).

Discovery of a run directory, the parsers for the two free-text records GACPD
emits, and unified-diff parsing for its hunk artifacts.
"""

from salp.ingest.diffs import (
    HunkHeader,
    hunk_side,
    parse_hunk_header,
    split_patch,
)
from salp.ingest.gacpd import (
    Classification,
    GACPDFile,
    GACPDPullRequest,
    HunkArtifacts,
    discover_pull_requests,
    load_pull_request,
)
from salp.ingest.records import (
    HunkSimilarity,
    LocalizationFacts,
    PullRequestMetadata,
    parse_pr_results,
    parse_results,
)

__all__ = [
    "Classification",
    "GACPDFile",
    "GACPDPullRequest",
    "HunkArtifacts",
    "HunkHeader",
    "HunkSimilarity",
    "LocalizationFacts",
    "PullRequestMetadata",
    "discover_pull_requests",
    "hunk_side",
    "load_pull_request",
    "parse_hunk_header",
    "parse_pr_results",
    "parse_results",
    "split_patch",
]
