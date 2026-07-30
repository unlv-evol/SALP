"""Mandatory validity conditions."""

from __future__ import annotations

from salp.models import (
    FOUNDATIONAL_SETS,
    SAP,
    Category,
    EvidenceState,
    elements_for,
)

# Refs an index object may carry that are resolved elsewhere than SAP.payloads:
# per-category evidence documents are written by the serializer, not carried as
# payload bodies.
_SERIALIZER_WRITTEN = (".json",)


def validate_sap(sap: SAP) -> list[str]:
    """Return a list of validation errors (empty means valid)."""
    errors: list[str] = []

    seen_hunks: set[str] = set()
    foundational = FOUNDATIONAL_SETS.get(sap.change_type, frozenset())

    for hunk in sap.hunks:
        if hunk.hunk_id in seen_hunks:
            errors.append(f"duplicate hunk id: {hunk.hunk_id}")
        seen_hunks.add(hunk.hunk_id)

        # the transformation must reference an existing function payload
        if hunk.transformation.fn_id not in sap.functions:
            errors.append(f"{hunk.hunk_id}: unknown fn_id {hunk.transformation.fn_id}")

        # every category must carry an outcome for every required element
        for name, ce in hunk.categories.items():
            expected = {s.element_id for s in elements_for(Category(name))}
            recorded = {e.object_type.split(".", 1)[-1] for e in ce.elements}
            if missing := expected - recorded:
                errors.append(
                    f"{hunk.hunk_id}/{name}: no outcome recorded for "
                    f"{', '.join(sorted(missing))}"
                )

        # every foundational category must be PRESENT for a mapped hunk
        for cat in foundational:
            found = hunk.categories.get(cat.value)
            if found is None or not found.elements:
                errors.append(f"{hunk.hunk_id}: missing foundational category {cat.value}")
            elif not any(e.state is EvidenceState.PRESENT for e in found.elements):
                errors.append(
                    f"{hunk.hunk_id}: foundational category {cat.value} is not PRESENT"
                )

        # unique object ids within the hunk
        ids = [e.object_id for ce in hunk.categories.values() for e in ce.elements]
        if len(ids) != len(set(ids)):
            errors.append(f"{hunk.hunk_id}: duplicate evidence object ids")

        # every payload reference resolves to a registered payload
        for ce in hunk.categories.values():
            for e in ce.elements:
                ref = e.payload_ref
                if ref is None or ref.endswith(_SERIALIZER_WRITTEN):
                    continue
                resolved = ref if ref in sap.payloads else f"hunks/{hunk.hunk_id}/{ref}"
                if resolved not in sap.payloads:
                    errors.append(f"{hunk.hunk_id}: unresolved payload ref {ref!r}")

    # composite ordering must be total over the hunks and free of duplicates
    if len(sap.hunk_order) != len(set(sap.hunk_order)):
        errors.append("hunk_order repeats a hunk")
    if set(sap.hunk_order) != seen_hunks:
        errors.append("hunk_order does not match the set of hunks")

    return errors
