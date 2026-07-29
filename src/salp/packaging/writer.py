"""Serialize SAPs to the canonical on-disk physical layout.

Directory grammar (per SAP spec, file-scoped MO minting)::

    <run>/PR-<n>/
        pr.json
        _context/<file>                     # NA/ED payloads, never minted as SAPs
        sap-<file>/
            sap.json                        # manifest: change type, hunk list
            characterization.json           # per-hunk profiles + aggregate
            provenance.json                 # SAP-level provenance + repo-state pin
            change.json                     # PR/commit metadata; hunk ordering
            functions/<fn_id>/
                source.before.<ext>         # f_s
                source.after.<ext>          # f'_s
                target.<ext>                # f_t
                ast.json                    # normalized AST
                structure.json              # enclosing class/package, spans
            hunks/<hunk_id>/
                hunk.json                   # INDEX SPINE
                hunk.diff
                edit_region.json
                transformation.json
                localization.json
                refactorings.json
                compatibility.json
                surrounding.json
                verification.json
                provenance.json

Index files carry no program text; payloads carry nothing but. Payload bodies
travel on ``SAP.payloads`` keyed by the same SAP-relative path an index object
stores in ``payload_ref``, so writing them is a straight dump and every
reference resolves by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

from salp.characterization import CharacterizationProfile
from salp.models import SAP, Category, CategoryEvidence, EvidenceState, PRGroup

# Category -> the hunk-level evidence file the specification names for it.
# Structural evidence is function-scoped (functions/<fn_id>/structure.json) and
# standalone evidence lives under standalone/, so neither appears here.
_HUNK_EVIDENCE_FILES: dict[Category, str] = {
    Category.SOURCE_CHANGE: "edit_region.json",
    Category.TARGET_LOCALIZATION: "localization.json",
    Category.FUNCTION_TRANSFORMATION: "transformation.json",
    Category.REFACTORING: "refactorings.json",
    Category.COMPATIBILITY: "compatibility.json",
    Category.SURROUNDING: "surrounding.json",
    Category.VERIFICATION: "verification.json",
}


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    path.write_text(json.dumps(obj, indent=2, sort_keys=False), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def category_state(ce: CategoryEvidence) -> EvidenceState:
    """The category-level state summarising its required elements.

    PRESENT as soon as any element was recovered; VERIFIED_ABSENT only when the
    whole investigation completed and found nothing; UNAVAILABLE otherwise --
    including a mix of verified absence and unresolved elements, which is not a
    completed investigation.
    """
    states = {e.state for e in ce.elements}
    if EvidenceState.PRESENT in states:
        return EvidenceState.PRESENT
    if states == {EvidenceState.VERIFIED_ABSENT}:
        return EvidenceState.VERIFIED_ABSENT
    return EvidenceState.UNAVAILABLE


def category_confidence(ce: CategoryEvidence) -> float | None:
    """Per-category confidence for the index: mean representation of what was recovered.

    Undefined when no element is PRESENT, matching category Fidelity. Candidate
    ranking reads this from the index without loading any payload.
    """
    present = [e for e in ce.elements if e.state is EvidenceState.PRESENT]
    if not present:
        return None
    return round(sum(e.representation for e in present) / len(present), 4)


def _evidence_document(ce: CategoryEvidence) -> dict[str, object]:
    """The per-category evidence file: element states, representation, diagnostics."""
    return {
        "category": ce.category.value,
        "state": category_state(ce).value,
        "confidence": category_confidence(ce),
        "blocking_conflict": any(e.blocking_conflict for e in ce.elements),
        "elements": [
            {
                "object_id": e.object_id,
                "element": e.object_type,
                "state": e.state.value,
                "representation": e.representation if e.state is EvidenceState.PRESENT else None,
                "payload_ref": e.payload_ref,
                "attributes": e.attributes,
                "blocking_conflict": e.blocking_conflict,
                "provenance": e.provenance.model_dump(mode="json") if e.provenance else None,
            }
            for e in ce.elements
        ],
    }


def hunk_index(sap: SAP, hunk_id: str) -> dict[str, object]:
    """Build the ``hunk.json`` index spine for one hunk.

    Small, program-text free, and total: every category the SAP carries appears
    with an explicit state, so evidence selection can prove a category was
    considered rather than merely missing.
    """
    hunk = sap.hunk(hunk_id)
    fn = sap.functions.get(hunk.transformation.fn_id)

    evidence: dict[str, object] = {}
    for name, ce in hunk.categories.items():
        cat = Category(name)
        entry: dict[str, object] = {
            "state": category_state(ce).value,
            "ref": _HUNK_EVIDENCE_FILES.get(cat),
            "confidence": category_confidence(ce),
            "blocking_conflict": any(e.blocking_conflict for e in ce.elements),
        }
        if cat is Category.STRUCTURAL and fn is not None:
            entry["ref"] = fn.structure_ref
        if category_state(ce) is EvidenceState.UNAVAILABLE:
            entry["ref"] = None
            reasons = {
                e.provenance.diagnostics
                for e in ce.elements
                if e.state is EvidenceState.UNAVAILABLE
                and e.provenance
                and e.provenance.diagnostics
            }
            entry["reason"] = "; ".join(sorted(r for r in reasons if r)) or None
        evidence[name] = entry

    transformation: dict[str, object] = {"edit_regions": hunk.transformation.edit_regions}
    if fn is not None:
        transformation |= {
            "f_s_before": fn.source_before_ref if fn.has_source_before else None,
            "f_s_after": fn.source_after_ref if fn.has_source_after else None,
            "f_t": fn.target_ref if fn.has_target else None,
        }

    return {
        "hunk_id": hunk.hunk_id,
        "change_id": sap.change_id,
        "sap_id": sap.sap_id,
        "change_type": sap.change_type.value,
        "schema_version": sap.schema_version,
        "transformation": transformation,
        "evidence": evidence,
        "relationships": [
            {"from": r.src, "rel": r.rel, "to": r.dst, "state": r.state, "evidence": r.evidence}
            for r in hunk.relationships
        ],
        "characterization_ref": f"../../characterization.json#{hunk.hunk_id}",
    }


def characterization_document(
    sap: SAP, profiles: dict[str, CharacterizationProfile]
) -> dict[str, object]:
    """Per-hunk profiles plus the SAP-level aggregate.

    A composite SAP's Readiness is the minimum over its constituent hunks, and
    the profile records which hunk determined that minimum so the bound is
    traceable rather than merely asserted.
    """
    aggregate: dict[str, object] = {}
    if profiles:
        governing = min(profiles.items(), key=lambda kv: (kv[1].readiness_final, kv[0]))
        aggregate = {
            "readiness": governing[1].readiness_final.name,
            "determined_by_hunk": governing[0],
            "rule": "minimum over constituent hunks",
            "hunk_count": len(profiles),
        }
    return {
        "sap_id": sap.sap_id,
        "change_type": sap.change_type.value,
        "aggregate": aggregate,
        "hunks": {hid: p.model_dump(mode="json") for hid, p in profiles.items()},
    }


def write_sap(
    sap: SAP,
    sap_dir: Path,
    profiles: dict[str, CharacterizationProfile] | None = None,
) -> None:
    """Write one SAP directory, index and payloads."""
    _write_json(sap_dir / "sap.json", {
        "sap_id": sap.sap_id,
        "change_id": sap.change_id,
        "change_type": sap.change_type.value,
        "schema_version": sap.schema_version,
        "source_file": sap.source_file,
        "target_file": sap.target_file,
        "composite": sap.is_composite,
        "hunks": [h.hunk_id for h in sap.hunks],
        "functions": sorted(sap.functions),
    })
    _write_json(sap_dir / "change.json", {
        "change_id": sap.change_id,
        "source_file": sap.source_file,
        "target_file": sap.target_file,
        "hunks": [h.hunk_id for h in sap.hunks],
        "hunk_order": sap.hunk_order,
        "composite": sap.is_composite,
    })
    _write_json(
        sap_dir / "provenance.json",
        sap.provenance.model_dump(mode="json") if sap.provenance else {},
    )

    # --- payloads: raw source files, written verbatim at their reference path --
    for ref, content in sap.payloads.items():
        _write_text(sap_dir / ref, content)

    # --- function pool ---------------------------------------------------------
    for fn in sap.functions.values():
        (sap_dir / fn.dir_ref).mkdir(parents=True, exist_ok=True)

    # --- per-hunk index + evidence documents -----------------------------------
    for hunk in sap.hunks:
        hdir = sap_dir / "hunks" / hunk.hunk_id
        _write_json(hdir / "hunk.json", hunk_index(sap, hunk.hunk_id))
        _write_json(
            hdir / "provenance.json",
            hunk.provenance.model_dump(mode="json") if hunk.provenance else {},
        )
        for name, ce in hunk.categories.items():
            cat = Category(name)
            if filename := _HUNK_EVIDENCE_FILES.get(cat):
                _write_json(hdir / filename, _evidence_document(ce))

    _write_structural_evidence(sap, sap_dir)

    if profiles:
        _write_json(sap_dir / "characterization.json", characterization_document(sap, profiles))


def _write_structural_evidence(sap: SAP, sap_dir: Path) -> None:
    """Write function-scoped structural evidence.

    Structural payloads are stored once per function and shared by every hunk in
    it, so the evidence document is written at function level. The first hunk
    that actually recovered structure wins; failing that, the first hunk's
    (UNAVAILABLE) result is recorded so the category is never silently missing.
    """
    for fn in sap.functions.values():
        candidates = [
            h.categories[Category.STRUCTURAL.value]
            for h in sap.hunks
            if h.transformation.fn_id == fn.fn_id
            and Category.STRUCTURAL.value in h.categories
        ]
        if not candidates:
            continue
        chosen = next(
            (c for c in candidates if category_state(c) is not EvidenceState.UNAVAILABLE),
            candidates[0],
        )
        _write_json(sap_dir / fn.structure_ref, _evidence_document(chosen))


def write_pr_group(group: PRGroup, pr_dir: Path) -> None:
    """Write the PR manifest and the ``_context/`` payloads it references."""
    for ref, content in group.payloads.items():
        _write_text(pr_dir / ref, content)
    _write_json(pr_dir / "pr.json", group)
