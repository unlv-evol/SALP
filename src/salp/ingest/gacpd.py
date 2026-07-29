"""Discovery: a GACPD run directory into pull requests, files, and hunks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from salp.ingest.records import (
    LocalizationFacts,
    PullRequestMetadata,
    parse_pr_results,
    parse_results,
)

# Values are matched with horizontal whitespace only. A field left blank --
# GACPD emits "PR Title:" with nothing after it -- must stay empty rather than
# swallowing the next line, which is what a newline-crossing \s* would do.


Classification = str  # "MO" | "NA" | "ED"


_HUNK_RE = re.compile(r"hunk_(\d+)_")


# GACPD tooling artifacts, excluded from every SAP. Matched as whole path
# components: a source file legitimately living under a directory called
# "reports" must not be dropped.
_IGNORE = frozenset({"__MACOSX", ".DS_Store", "reports", ".jscpd.json"})


@dataclass
class HunkArtifacts:
    hunk_id: str
    context: Path | None = None
    full_del: Path | None = None
    full_add: Path | None = None
    additions: Path | None = None
    deletions: Path | None = None


@dataclass
class GACPDFile:
    name: str
    classification: Classification
    file_dir: Path
    patch: Path | None = None
    target_file: Path | None = None  # cmp/<File>
    hunks: list[HunkArtifacts] = field(default_factory=list)
    # A whole-file payload for NA/ED siblings, copied into the PR's _context/.
    context_payload: Path | None = None
    localization: LocalizationFacts = field(default_factory=LocalizationFacts)

    @property
    def ext(self) -> str:
        """Source language extension, recovered from the artifacts GACPD emitted.

        Payloads are written as raw source files with the correct extension, so
        the extension has to come from real artifacts rather than the directory
        name, which GACPD flattens the whole source path into
        (``streams_src_main_..._CombinedKey_java``).
        """
        if self.localization.source_path:
            suffix = Path(self.localization.source_path).suffix
            if suffix:
                return suffix.lstrip(".")
        for cand in (self.target_file, self.context_payload):
            if cand is not None and cand.suffix:
                return cand.suffix.lstrip(".")
        for h in self.hunks:
            for cand in (h.full_del, h.full_add, h.context, h.additions, h.deletions):
                if cand is not None and cand.suffix:
                    return cand.suffix.lstrip(".")
        if self.patch is not None:
            stem = Path(self.patch.stem)
            if stem.suffix:
                return stem.suffix.lstrip(".")
        return "txt"

    @property
    def display_name(self) -> str:
        """The file's real name, e.g. ``CombinedKey.java``.

        The directory name is the flattened source path and unusable, so the
        name comes from the path ``results.txt`` reports, falling back to the
        artifacts GACPD emitted.
        """
        if self.localization.source_path:
            return Path(self.localization.source_path).name
        if self.target_file is not None:
            return self.target_file.name
        if self.context_payload is not None:
            return self.context_payload.name
        if self.patch is not None:
            return self.patch.stem if "." in self.patch.stem else f"{self.patch.stem}.{self.ext}"
        return self.name

    @property
    def source_path(self) -> str:
        """The repository-relative source path, e.g. ``streams/src/.../CombinedKey.java``."""
        return self.localization.source_path or self.display_name


@dataclass
class GACPDPullRequest:
    pr_id: str
    pr_dir: Path
    results_file: Path | None = None
    files: list[GACPDFile] = field(default_factory=list)
    metadata: PullRequestMetadata = field(default_factory=PullRequestMetadata)

    @property
    def mo_files(self) -> list[GACPDFile]:
        return [f for f in self.files if f.classification.upper() == "MO"]

    @property
    def context_files(self) -> list[GACPDFile]:
        return [f for f in self.files if f.classification.upper() in ("NA", "ED")]


def _is_ignored(path: Path) -> bool:
    return any(part in _IGNORE for part in path.parts)


def _collect_hunks(src_dir: Path) -> list[HunkArtifacts]:
    by_id: dict[str, HunkArtifacts] = {}
    if not src_dir.is_dir():
        return []
    for p in sorted(src_dir.iterdir()):
        m = _HUNK_RE.search(p.name)
        if not m:
            continue
        hid = f"H-{int(m.group(1))}"
        h = by_id.setdefault(hid, HunkArtifacts(hunk_id=hid))
        if "context" in p.name:
            h.context = p
        elif "full_del" in p.name:
            h.full_del = p
        elif "full_add" in p.name:
            h.full_add = p
        elif "additions" in p.name:
            h.additions = p
        elif "deletions" in p.name:
            h.deletions = p
    return [by_id[k] for k in sorted(by_id, key=lambda s: int(s.split("-")[1]))]


def _load_file(file_dir: Path) -> GACPDFile | None:
    results = file_dir / "results.txt"
    facts = parse_results(results if results.is_file() else None)
    if facts.classification is None:
        return None

    patch = None
    src = file_dir / "src"
    if src.is_dir():
        patches = sorted(src.glob("*.patch"))
        patch = patches[0] if patches else None

    target = None
    cmp = file_dir / "cmp"
    if cmp.is_dir():
        cands = sorted(p for p in cmp.iterdir() if p.is_file())
        target = cands[0] if cands else None

    return GACPDFile(
        name=file_dir.name,
        classification=facts.classification,
        file_dir=file_dir,
        patch=patch,
        target_file=target,
        hunks=_collect_hunks(src),
        context_payload=_context_payload(file_dir, target),
        localization=facts,
    )


def _context_payload(file_dir: Path, target: Path | None) -> Path | None:
    """Pick the whole-file payload to retain for an NA/ED sibling.

    Preference order: the divergent-repository copy under ``cmp/``, then a
    whole source file under ``src/`` (excluding the patch and the per-hunk
    fragments, which are not whole files). Returns None when GACPD retained
    nothing copyable, so the manifest can record the gap instead of emitting a
    dangling ``_context/`` reference.
    """
    if target is not None:
        return target
    src = file_dir / "src"
    if not src.is_dir():
        return None
    for p in sorted(src.iterdir()):
        if not p.is_file() or p.suffix == ".patch" or _HUNK_RE.search(p.name):
            continue
        return p
    return None


def load_pull_request(pr_dir: Path) -> GACPDPullRequest:
    """Load one ``<PR>_MO`` directory."""
    results_file = pr_dir / "pr_results.txt"
    pr = GACPDPullRequest(
        pr_id=pr_dir.name,
        pr_dir=pr_dir,
        results_file=results_file if results_file.is_file() else None,
        metadata=parse_pr_results(
            results_file if results_file.is_file() else None,
            pr_dir_name=pr_dir.name,
        ),
    )
    for bucket in ("MO", "NA", "ED"):
        bdir = pr_dir / bucket
        if not bdir.is_dir():
            continue
        for file_dir in sorted(bdir.iterdir()):
            if not file_dir.is_dir() or _is_ignored(file_dir):
                continue
            gf = _load_file(file_dir)
            if gf is not None:
                pr.files.append(gf)
    _promote_repository_pair(pr)
    return pr


def _promote_repository_pair(pr: GACPDPullRequest) -> None:
    """Lift the repository pair from the per-file records onto the pull request.

    ``pr_results.txt`` never names the divergent repository, and the run
    directory abbreviates it, so the pair is recovered from the first file whose
    ``results.txt`` reported it. A pull request whose files disagree is recorded
    as a diagnostic rather than silently resolved.
    """
    pairs = {
        (f.localization.source_repo, f.localization.target_repo)
        for f in pr.files
        if f.localization.source_repo and f.localization.target_repo
    }
    if not pairs:
        pr.metadata.diagnostics.append("no repository pair reported by any results.txt")
        return
    source, target = sorted(pairs)[0]
    pr.metadata.source_repo = pr.metadata.source_repo or source
    pr.metadata.target_repo = pr.metadata.target_repo or target
    if len(pairs) > 1:
        pr.metadata.diagnostics.append(
            f"files disagree on the repository pair: {sorted(pairs)}; using {source}->{target}"
        )


def discover_pull_requests(run_dir: Path) -> list[GACPDPullRequest]:
    """Find every ``*_MO`` PR directory beneath a GACPD run directory."""
    prs: list[GACPDPullRequest] = []
    for pr_dir in sorted(run_dir.rglob("*_MO")):
        if not pr_dir.is_dir() or _is_ignored(pr_dir):
            continue
        if (pr_dir / "pr_results.txt").is_file() or any(
            (pr_dir / b).is_dir() for b in ("MO", "NA", "ED")
        ):
            prs.append(load_pull_request(pr_dir))
    return prs
