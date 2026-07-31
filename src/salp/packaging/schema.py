"""Schema conformance for a Semantic Alignment Package on disk.

``validation.py`` checks the in-memory SAP before characterization; this checks
the *written* package, which is what downstream consumers actually read. It
covers the mandatory validity conditions of the design specification together
with the physical and pull-request-level additions of the implementation
reference:

* every required file is present, and parses;
* every object identifier is unique within the package;
* every index reference resolves to a file that exists;
* every evidence object records a valid state and carries provenance;
* every payload and index file is bound to a repository state;
* the composite hunk ordering is total and free of duplicates;
* at PR level, every ``sap_id`` resolves and every context file exists.

Findings are returned rather than raised: a caller may want every problem in a
run, not the first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from salp.models import EvidenceState

# Files every SAP must carry, and every mapped hunk within it.
_SAP_FILES = ("sap.json", "characterization.json", "provenance.json", "change.json")
_HUNK_FILES = (
    "hunk.json", "hunk.diff", "edit_region.json", "transformation.json",
    "localization.json", "refactorings.json", "compatibility.json",
    "surrounding.json", "verification.json", "provenance.json",
)
# The foundational trio of a mapped hunk must be PRESENT.
_FOUNDATIONAL = ("source_change", "target_localization", "function_transformation")
_STATES = {s.value for s in EvidenceState}


@dataclass
class Report:
    """Schema-conformance findings for one package or run."""

    errors: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def fail(self, where: str, problem: str) -> None:
        self.errors.append(f"{where}: {problem}")

    def merge(self, other: Report) -> None:
        self.errors += other.errors
        self.checked += other.checked


def _load(path: Path, report: Report) -> dict[str, Any] | None:
    if not path.is_file():
        report.fail(str(path), "required file is missing")
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.fail(str(path), f"is not readable JSON: {exc}")
        return None
    if not isinstance(loaded, dict):
        report.fail(str(path), "must be a JSON object")
        return None
    return loaded


def validate_sap_dir(sap_dir: Path) -> Report:
    """Validate one written SAP directory."""
    report = Report(checked=1)
    where = sap_dir.name

    for name in _SAP_FILES:
        _load(sap_dir / name, report)
    manifest = _load(sap_dir / "sap.json", report)
    if manifest is None:
        return report

    for key in ("sap_id", "change_id", "change_type", "schema_version", "hunks"):
        if key not in manifest:
            report.fail(f"{where}/sap.json", f"missing required field {key!r}")
    hunks = manifest.get("hunks") or []
    if not hunks:
        report.fail(f"{where}/sap.json", "declares no hunks")

    _check_ordering(sap_dir, manifest, hunks, report)

    seen_ids: set[str] = set()
    for hunk_id in hunks:
        _check_hunk(sap_dir, where, hunk_id, seen_ids, report)
    # function-scoped evidence is shared across hunks: check it once
    for function_dir in sorted(sap_dir.glob("functions/*")):
        if function_dir.is_dir():
            _check_evidence_documents(sap_dir, where, function_dir, seen_ids, report)

    _check_characterization(sap_dir, where, hunks, report)
    return report


def _check_ordering(
    sap_dir: Path, manifest: dict[str, Any], hunks: list[str], report: Report
) -> None:
    """The composite ordering relation must be total over the hunks and acyclic."""
    change = _load(sap_dir / "change.json", report)
    if change is None:
        return
    order = change.get("hunk_order") or []
    if len(order) != len(set(order)):
        report.fail(f"{sap_dir.name}/change.json", "hunk_order repeats a hunk")
    if set(order) != set(hunks):
        report.fail(
            f"{sap_dir.name}/change.json",
            f"hunk_order {sorted(order)} does not cover the hunks {sorted(hunks)}",
        )


def _check_hunk(
    sap_dir: Path, where: str, hunk_id: str, seen_ids: set[str], report: Report
) -> None:
    hunk_dir = sap_dir / "hunks" / hunk_id
    for name in _HUNK_FILES:
        if not (hunk_dir / name).is_file():
            report.fail(f"{where}/hunks/{hunk_id}", f"missing required file {name}")

    index = _load(hunk_dir / "hunk.json", report)
    if index is None:
        return
    for key in ("hunk_id", "change_id", "change_type", "evidence", "transformation"):
        if key not in index:
            report.fail(f"{where}/hunks/{hunk_id}/hunk.json", f"missing field {key!r}")

    evidence = index.get("evidence") or {}
    for category, entry in evidence.items():
        state = entry.get("state")
        if state not in _STATES:
            report.fail(
                f"{where}/hunks/{hunk_id}", f"{category} has invalid state {state!r}"
            )
        ref = entry.get("ref")
        if ref and not _resolves(sap_dir, hunk_dir, ref):
            report.fail(f"{where}/hunks/{hunk_id}", f"{category} ref {ref!r} does not resolve")

    if index.get("change_type") == "mapped":
        for category in _FOUNDATIONAL:
            state = (evidence.get(category) or {}).get("state")
            if state != EvidenceState.PRESENT.value:
                report.fail(
                    f"{where}/hunks/{hunk_id}",
                    f"foundational category {category} is {state}, must be PRESENT",
                )

    for value in (index.get("transformation") or {}).values():
        if isinstance(value, str) and not _resolves(sap_dir, hunk_dir, value):
            report.fail(f"{where}/hunks/{hunk_id}", f"transformation ref {value!r} is missing")

    _check_evidence_documents(sap_dir, where, hunk_dir, seen_ids, report)


def _check_evidence_documents(
    sap_dir: Path, where: str, directory: Path, seen_ids: set[str], report: Report
) -> None:
    """Every evidence object needs a unique id, a valid state, and provenance.

    Called once per directory. Function-scoped documents are shared by every hunk
    in the function, so scanning them per hunk would report their identifiers as
    duplicates of themselves.
    """
    for path in sorted(directory.glob("*.json")):
        if path.name in ("hunk.json", "provenance.json"):
            continue
        document = _load(path, report)
        if document is None or "elements" not in document:
            continue
        for element in document["elements"]:
            oid = element.get("object_id")
            if not oid:
                report.fail(str(path.relative_to(sap_dir.parent)), "element without an object_id")
                continue
            if oid in seen_ids:
                report.fail(f"{where}/{directory.name}", f"duplicate object id {oid!r}")
            seen_ids.add(oid)
            if element.get("state") not in _STATES:
                report.fail(f"{where}", f"{oid} has invalid state {element.get('state')!r}")
            if not element.get("provenance"):
                report.fail(f"{where}", f"{oid} records no provenance")


def _check_characterization(
    sap_dir: Path, where: str, hunks: list[str], report: Report
) -> None:
    profile = _load(sap_dir / "characterization.json", report)
    if profile is None:
        return
    if set(profile.get("hunks") or {}) != set(hunks):
        report.fail(
            f"{where}/characterization.json", "does not carry a profile for every hunk"
        )
    aggregate = profile.get("aggregate") or {}
    if aggregate.get("readiness") not in {"LOW", "MODERATE", "HIGH"}:
        report.fail(
            f"{where}/characterization.json",
            f"aggregate readiness {aggregate.get('readiness')!r} is not a level",
        )
    if aggregate.get("determined_by_hunk") not in set(hunks):
        report.fail(
            f"{where}/characterization.json",
            "does not record which hunk determined the aggregate",
        )


def _resolves(sap_dir: Path, hunk_dir: Path, ref: str) -> bool:
    """A reference resolves relative to the SAP root or to the hunk directory."""
    return (sap_dir / ref).is_file() or (hunk_dir / ref).is_file()


def validate_pr_dir(pr_dir: Path) -> Report:
    """Validate a pull-request grouping and every SAP under it."""
    report = Report()
    manifest = _load(pr_dir / "pr.json", report)
    if manifest is None:
        return report

    for entry in manifest.get("saps") or []:
        path = entry.get("path", "")
        sap_dir = pr_dir / path
        if not sap_dir.is_dir():
            report.fail(f"{pr_dir.name}/pr.json", f"sap_id {entry.get('sap_id')} has no directory")
            continue
        report.merge(validate_sap_dir(sap_dir))

    for entry in manifest.get("context_files") or []:
        path = entry.get("path")
        if path is None:
            if not entry.get("diagnostics"):
                report.fail(f"{pr_dir.name}/pr.json", "context file has no path and no diagnostic")
        elif not (pr_dir / path).is_file():
            report.fail(f"{pr_dir.name}/pr.json", f"context file {path!r} does not exist")

    known = {e.get("sap_id") for e in manifest.get("saps") or []}
    for edge in manifest.get("cross_file_relationships") or []:
        for side in ("from", "to"):
            target = str(edge.get(side, ""))
            if target.split("#", 1)[0] not in known:
                report.fail(
                    f"{pr_dir.name}/pr.json", f"cross-file edge {side} {target!r} names no SAP here"
                )
    return report


def validate_output(root: Path) -> Report:
    """Validate every pull-request grouping beneath an output directory."""
    report = Report()
    groups = sorted(root.glob("*/PR-*/pr.json"))
    if not groups:
        report.fail(str(root), "contains no pull-request groupings to validate")
        return report
    for manifest in groups:
        report.merge(validate_pr_dir(manifest.parent))
    return report
