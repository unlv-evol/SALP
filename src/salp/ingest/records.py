"""Parsers for the free-text records GACPD emits."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from salp.models import RepositoryStatePin

# Values are matched with horizontal whitespace only. A field left blank --
# GACPD emits "PR Title:" with nothing after it -- must stay empty rather than
# swallowing the next line, which is what a newline-crossing \s* would do.


_H = r"[ \t]*"


_FLAGS = re.IGNORECASE | re.MULTILINE


def _line(label: str, value: str = r".*?") -> re.Pattern[str]:
    return re.compile(rf"^{_H}{label}{_H}:{_H}({value}){_H}$", _FLAGS)


# --- pr_results.json ----------------------------------------------------------
_PR_NUMBER = "classifiedPR"

_PR_TITLE = "prTitle"


_PR_LOCATION ="prLocation"


_DIVERGENCE_DATE = "repoDivergenceDate"


_CUTOFF_DATE = "cutoffDate"


# --- results.json -------------------------------------------------------------
_MAINLINE = "mainline"


_DIVERGENT_REPO = "divergentRepo"


_SOURCE_PATH = "fileName"


_DIVERGENT_PATH = "divergentPath"


_CLASSIFICATION = "classification"

_SIMILARITYCHECK = "similarityChecks"

_CHECKNAME = "checkName"

# e.g. "src/hunk_1_deletions.java (30) - has a similarity of: 100%"
_HUNK = re.compile(
    r"^hunk_(?P<number>\d+)_(?P<mode>[a-zA-Z0-9_-]+)\.[a-zA-Z0-9]+$",
    re.IGNORECASE,
)


_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _read(path: Path | None) -> str:
    """Read a GACPD record, normalizing its CRLF line endings."""
    if path is None or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _field(pattern: re.Pattern[str], text: str) -> str | None:
    """The value of a labelled line, or None when absent or blank."""
    m = pattern.search(text)
    if m is None:
        return None
    return m.group(1).strip() or None


@dataclass
class HunkSimilarity:
    """jscpd similarity for one hunk, per block kind and minimum-token threshold.

    GACPD runs the clone detector at descending token thresholds (50, 40, 30,
    20) and reports each block separately. The *deletions* block is the anchor:
    it is the code region the change modifies, so its presence in the target is
    what localization asserts. The *additions* block is the replacement, which
    is not expected to exist in the target yet -- a 0% additions similarity is
    the divergence the adaptation must reconcile, not a localization failure.
    """

    hunk_id: str
    additions: dict[int, float] = field(default_factory=dict)
    deletions: dict[int, float] = field(default_factory=dict)

    @staticmethod
    def _mean(scores: dict[int, float]) -> float | None:
        return sum(scores.values()) / len(scores) if scores else None

    @property
    def confidence(self) -> float | None:
        """Alignment confidence in [0, 1] for this hunk.

        The mean deletion similarity across the thresholds GACPD reported. A
        block matching at every threshold scores 1.0; one matching only at the
        coarsest threshold scores proportionally less, which is how a genuine
        but weak alignment is recorded as *reduced confidence* rather than as a
        failed localization. Falls back to the additions block only for pure
        insertions, which report no deletion anchor at all -- a deletion anchor
        that was measured and scored zero is a real result, not a missing one.
        """
        if self.deletions:
            return self._mean(self.deletions)
        return self._mean(self.additions)


@dataclass
class PullRequestMetadata:
    """Pull-request identity, as reported by ``pr_results.json``.

    The repository pair is not in this record; it is recovered per file from
    ``results.json`` and promoted to the pull request by the caller.
    """

    number: str | None = None
    title: str | None = None
    url: str | None = None
    source_repo: str | None = None  # mainline
    target_repo: str | None = None  # divergent
    divergence_date: str | None = None
    cutoff_date: str | None = None
    # The unreduced timestamps, kept because resolving a cutoff to a commit
    # needs the time of day: "2022-12-02" alone means midnight at its start.
    divergence_timestamp: str | None = None
    cutoff_timestamp: str | None = None
    diagnostics: list[str] = field(default_factory=list)

    def pin(self, repo: str | None) -> RepositoryStatePin | None:
        """A partial, date-based repository-state binding.

        Used when no local clone is available to resolve a commit: the SAP
        records the repository and flags the missing binding rather than
        inventing one.
        """
        if not repo:
            return None
        return RepositoryStatePin(
            repo=repo,
            diagnostics="GACPD emits dates, not commit SHAs; no local clone to resolve against",
        )

    def as_manifest_block(self) -> dict[str, object]:
        """The ``pull_request`` block of the PR manifest."""
        return {
            "number": int(self.number) if self.number and self.number.isdigit() else self.number,
            "title": self.title,
            "url": self.url,
            "divergence_date": self.divergence_date,
            "cutoff_date": self.cutoff_date,
            "commit_binding": {
                "state": "UNAVAILABLE",
                "reason": "GACPD emits dates, not commit SHAs; pending SALP resolution",
            },
        }


def parse_pr_results(
    path: Path | None, *, pr_dir_name: str | None = None
) -> PullRequestMetadata:
    """Get the data from ``pr_results.json`` and save into pull-request metadata."""
    results = {}
    meta = PullRequestMetadata()

    if path is not None:
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw_data, dict):
                results = raw_data
            else:
                meta.diagnostics.append("pr_results.json is not a valid JSON object")
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            meta.diagnostics.append("pr_results.json absent or unreadable")
    else:
        meta.diagnostics.append("pr_results.json path not provided")

    # the record is authoritative; the directory name is the fallback
    meta.number = results.get(_PR_NUMBER)
    if meta.number is None and pr_dir_name and (m := re.search(r"(\d+)", pr_dir_name)):
        meta.number = m.group(1)
    meta.title = results.get(_PR_TITLE)
    meta.url = results.get(_PR_LOCATION)
    # dates are timestamps (2022-06-02T00:00:00Z); the calendar day is what the
    # manifest reports, the full timestamp is what resolves a commit
    meta.divergence_timestamp = results.get(_DIVERGENCE_DATE)
    meta.cutoff_timestamp = results.get(_CUTOFF_DATE)
    meta.divergence_date = _calendar_day(meta.divergence_timestamp)
    meta.cutoff_date = _calendar_day(meta.cutoff_timestamp)

    for label, value in (("title", meta.title), ("url", meta.url)):
        if value is None:
            meta.diagnostics.append(f"pr_results.json records no {label}")
    if not (meta.divergence_date and meta.cutoff_date):
        meta.diagnostics.append("incomplete divergence/cutoff dates; pin is unbound")
    return meta


def _calendar_day(timestamp: str | None) -> str | None:
    if timestamp is None:
        return None
    m = _ISO_DATE.search(timestamp)
    return m.group(1) if m else timestamp


@dataclass
class LocalizationFacts:
    """What a file's ``results.json`` reports about its target-side localization."""

    classification: str | None = None
    source_repo: str | None = None
    target_repo: str | None = None
    source_path: str | None = None
    divergent_path_raw: str | None = None
    divergent_path: str | None = None
    similarity: dict[str, HunkSimilarity] = field(default_factory=dict)

    def confidence(self, hunk_id: str) -> float | None:
        sim = self.similarity.get(hunk_id)
        return sim.confidence if sim else None

    def breakdown(self, hunk_id: str) -> dict[str, object] | None:
        """The full per-threshold similarity, retained alongside the aggregate."""
        sim = self.similarity.get(hunk_id)
        if sim is None:
            return None
        return {"additions": sim.additions, "deletions": sim.deletions}


def _repo_relative(raw: str | None, target_repo: str | None) -> str | None:
    """Strip GACPD's local working-directory prefix from a located target path.

    GACPD reports the path inside its own checkout, e.g.
    ``Results/Repos_files/<run>/linkedin/kafka/streams/.../CombinedKey.java``.
    Everything up to and including ``<owner>/<repo>/`` is scaffolding; what the
    SAP needs is the repository-relative path.
    """
    if not raw:
        return None
    if target_repo and f"/{target_repo}/" in f"/{raw}":
        return f"/{raw}".split(f"/{target_repo}/", 1)[1]
    return raw


def parse_results(path: Path | None) -> LocalizationFacts:
    """Parse a file's ``results.json``: repository pair, paths, and similarity."""
    results = {}
    facts = LocalizationFacts()

    if path is not None:
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw_data, dict):
                results = raw_data
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        
    
    if results.get(_CLASSIFICATION):
        facts.classification = results[_CLASSIFICATION].upper()
    facts.source_repo = results.get(_MAINLINE)
    facts.target_repo = results.get(_DIVERGENT_REPO)
    facts.source_path = results.get(_SOURCE_PATH)
    facts.divergent_path_raw = results.get(_DIVERGENT_PATH)
    facts.divergent_path = _repo_relative(facts.divergent_path_raw, facts.target_repo)

    if results.get(_SIMILARITYCHECK):
        for similarity_check in results[_SIMILARITYCHECK]:
            match = _HUNK.search(similarity_check.get(_CHECKNAME))
            if match:
                number,kind = match.groups()
            hunk_id = f"H-{int(number)}"
            sim = facts.similarity.setdefault(hunk_id, HunkSimilarity(hunk_id=hunk_id))
            scores = sim.deletions if kind.lower() == "deletions" else sim.additions
            scores[similarity_check['tokenSize']] = max(0.0, 
                min(1.0, float(similarity_check['similarityPercent']) / 100))
    return facts
