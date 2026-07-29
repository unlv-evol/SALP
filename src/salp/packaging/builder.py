"""SAP construction from a GACPD MO file."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from salp.analyzers import AnalysisContext, build_all
from salp.config import get_logger
from salp.ingest import (
    GACPDFile,
    GACPDPullRequest,
    HunkArtifacts,
    HunkHeader,
    hunk_side,
    parse_hunk_header,
    split_patch,
)
from salp.models import (
    SAP,
    ChangeType,
    FunctionPayload,
    Hunk,
    Provenance,
    Relationship,
    RepositoryStatePin,
    TransformationUnit,
)
from salp.repos import grep_files, read_dependencies, read_file

log = get_logger(__name__)


# What the GACPD hunk artifacts actually contain, recorded on every payload
# built from them so the approximation is never silently presented as exact.
_REGION_DIAGNOSTIC = (
    "payload covers the changed regions and their diff context, not whole "
    "function bodies; function-boundary expansion pending structural analysis"
)


def _read(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("could not read payload %s: %s", path, exc)
        return None


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "-", name).strip("-")[:60]


class _HunkSource:
    """One GACPD hunk with its artifacts read and its diff header parsed."""

    def __init__(self, artifacts: HunkArtifacts, index: int) -> None:
        self.artifacts = artifacts
        self.index = index
        self.hunk_id = artifacts.hunk_id
        self.before = _read(artifacts.full_del)
        self.after = _read(artifacts.full_add)
        self.diff: str | None = None
        self.derived: list[str] = []
        self.header: HunkHeader | None = parse_hunk_header(self.before) or parse_hunk_header(
            self.after
        )

    def attach_diff(self, diff: str | None) -> None:
        """Attach the hunk's own diff, recovering either side GACPD omitted.

        A pure-deletion hunk has no ``full_add`` and a pure insertion no
        ``full_del``, because there was nothing for GACPD to write. Both sides
        are still exactly recoverable from the diff, so the Transformation Unit
        stays complete instead of reporting a foundational gap that the evidence
        does not actually have.
        """
        self.diff = diff
        if diff is None:
            return
        if self.before is None and (before := hunk_side(diff, side="before")):
            self.before = before
            self.derived.append("f_s")
        if self.after is None and (after := hunk_side(diff, side="after")):
            self.after = after
            self.derived.append("f'_s")
        if self.header is None:
            self.header = parse_hunk_header(diff)

    @property
    def derivation_note(self) -> str | None:
        if not self.derived:
            return None
        return (
            f"{', '.join(self.derived)} reconstructed from the hunk diff; GACPD emitted no "
            "payload for that side because the hunk only adds or only deletes"
        )

    @property
    def group_key(self) -> str:
        """Which function-pool entry this hunk belongs to.

        Hunks reported under the same diff section heading occupy the same
        function. A hunk with no heading -- an edit outside any function, such
        as an import change -- groups only with itself.
        """
        heading = self.header.group_key() if self.header else None
        return heading or f"\x00hunk:{self.hunk_id}"


def _deps(
    cache_dir: Path | None, pin: RepositoryStatePin | None, path: str | None
) -> list[str] | None:
    return read_dependencies(cache_dir, pin, path) if cache_dir else None


def _covering_tests(
    cache_dir: Path | None, pin: RepositoryStatePin | None, entity: str
) -> list[str] | None:
    """Target-side tests referencing the edited entity, or None if unsearchable.

    None means the search could not run; an empty list means it ran and found no
    oracle, which is verified absence rather than a gap.
    """
    if cache_dir is None or pin is None or not pin.is_resolved or not entity:
        return None
    return grep_files(
        cache_dir, pin, entity, "*Test*.java", "*Tests.java", "*Spec*.java", "*Test*.scala"
    )


def _at_pin(
    cache_dir: Path | None, pin: RepositoryStatePin | None, path: str | None
) -> str | None:
    """Read a whole file at a pinned repository state, if one is bound."""
    if cache_dir is None or pin is None or not path:
        return None
    return read_file(cache_dir, pin, path)


def function_ids(sources: list[_HunkSource], file_stem: str) -> dict[str, str]:
    """Map each hunk to its function-pool identifier.

    Hunks are grouped by diff section heading; the entry is named after the
    declared function when the heading is a real declaration, and positionally
    otherwise, so a truncated or ambiguous heading never asserts a function name
    the evidence does not support.
    """
    groups: dict[str, list[_HunkSource]] = {}
    for source in sources:
        groups.setdefault(source.group_key, []).append(source)

    assigned: dict[str, str] = {}
    used: set[str] = set()
    for members in groups.values():
        first = members[0]
        name = first.header.declared_name() if first.header else None
        fn_id = f"{file_stem}_{_slug(name)}" if name else f"{file_stem}_fn{first.index}"
        while fn_id in used:  # distinct functions sharing a name stay distinct
            fn_id = f"{fn_id}_{first.index}"
        used.add(fn_id)
        for member in members:
            assigned[member.hunk_id] = fn_id
    return assigned


def _build_function_pool(
    sap: SAP,
    sources: list[_HunkSource],
    fn_ids: dict[str, str],
    ext: str,
    target_text: str | None,
) -> None:
    """Register one pool entry per distinct enclosing function.

    Hunks that occupy the same function contribute their regions to a single
    entry, in file order, each retaining its diff header so the concatenation
    stays self-describing. ``f_t`` is the located target file, shared by every
    function of this SAP.
    """
    grouped: dict[str, list[_HunkSource]] = {}
    for source in sources:
        grouped.setdefault(fn_ids[source.hunk_id], []).append(source)

    for fn_id, members in grouped.items():
        fn = FunctionPayload(fn_id=fn_id, ext=ext)
        before = [m.before for m in members if m.before]
        after = [m.after for m in members if m.after]
        if before:
            sap.add_payload(fn.source_before_ref, "\n".join(before))
            fn.has_source_before = True
        if after:
            sap.add_payload(fn.source_after_ref, "\n".join(after))
            fn.has_source_after = True
        if target_text is not None:
            sap.add_payload(fn.target_ref, target_text)
            fn.has_target = True
        sap.functions[fn_id] = fn


def _patch_slices(gf: GACPDFile, sources: list[_HunkSource]) -> dict[str, str]:
    """Map each hunk to its own slice of the whole-file patch.

    The SAP stores one ``hunk.diff`` per hunk, so the patch is split on its
    ``@@`` headers and matched to hunks by header. When the split does not line
    up with the hunks GACPD reported, every hunk falls back to the whole patch
    rather than being given a slice that might belong to a different region.
    """
    patch = _read(gf.patch)
    slices = split_patch(patch)
    if not slices:
        return {s.hunk_id: patch for s in sources if patch is not None}

    by_span = {(h.old_start, h.new_start): text for h, text in slices}
    matched: dict[str, str] = {}
    for source in sources:
        header = source.header
        key = (header.old_start, header.new_start) if header else None
        if key is not None and key in by_span:
            matched[source.hunk_id] = by_span[key]

    if len(matched) == len(sources):
        return matched
    if len(slices) == len(sources):  # same count, headers differ: trust file order
        return {s.hunk_id: text for s, (_, text) in zip(sources, slices, strict=True)}

    log.warning(
        "%s: patch has %d hunk(s) but GACPD reported %d; using the whole patch per hunk",
        gf.display_name, len(slices), len(sources),
    )
    return {s.hunk_id: patch for s in sources if patch is not None}


def _input_artifacts(gf: GACPDFile, ha: HunkArtifacts) -> list[str]:
    paths = [gf.patch, gf.target_file, ha.full_del, ha.full_add, ha.context]
    return [str(p) for p in paths if p is not None]


def _relationships(hunk_id: str, fn_id: str) -> list[Relationship]:
    """Typed edges from the edit region to the objects reduction should reach.

    Reduction is a reachability computation rooted at the edit region, so an
    object that no edge reaches is never materialized into the adaptation
    context. Enriched analyzers add their own edges (renamed_to, api_replaced,
    ...) as they land; the alignment edge is always present.
    """
    return [
        Relationship(src=f"{hunk_id}:ER-1", rel="aligned_to", dst=f"functions/{fn_id}"),
    ]


def build_sap(
    gf: GACPDFile,
    sap_id: str,
    *,
    change_id: str | None = None,
    pr: GACPDPullRequest | None = None,
    source_repo: str | None = None,
    target_repo: str | None = None,
    source_pin: RepositoryStatePin | None = None,
    target_pin: RepositoryStatePin | None = None,
    cache_dir: Path | None = None,
    refactorings: list[dict[str, Any]] | str | None = None,
) -> SAP:
    """Construct one file-scoped SAP from an MO GACPD file."""
    analyzers = build_all()
    ext = gf.ext
    file_stem = Path(gf.display_name).stem or gf.name

    meta = pr.metadata if pr else None
    source_repo = source_repo or gf.localization.source_repo or (meta.source_repo if meta else None)
    target_repo = target_repo or gf.localization.target_repo or (meta.target_repo if meta else None)
    source_pin = source_pin or (meta.pin(source_repo) if meta else None)
    target_pin = target_pin or (meta.pin(target_repo) if meta else None)

    sap = SAP(
        sap_id=sap_id,
        change_id=change_id or sap_id,
        change_type=ChangeType.MAPPED,
        source_file=gf.source_path,
        target_file=gf.localization.divergent_path,
        provenance=Provenance(
            analysis_component="sap-construction",
            analysis_tool="gacpd",
            input_artifacts=[str(gf.file_dir)],
            diagnostics="; ".join(meta.diagnostics) if meta and meta.diagnostics else None,
            repository_pin=source_pin,
        ),
    )

    sources = [_HunkSource(ha, i) for i, ha in enumerate(gf.hunks, start=1)]
    patch_slices = _patch_slices(gf, sources)
    for source in sources:
        source.attach_diff(patch_slices.get(source.hunk_id))

    target_text = _read(gf.target_file)
    fn_ids = function_ids(sources, file_stem)
    _build_function_pool(sap, sources, fn_ids, ext, target_text)

    context_names = [c.display_name for c in (pr.context_files if pr else [])]
    hunk_order = [s.hunk_id for s in sources]

    # Whole files at the pinned states, for analyses that need the enclosing
    # structure GACPD's hunk regions cannot carry.
    source_file_text = _at_pin(cache_dir, source_pin, gf.source_path)
    target_file_text = _at_pin(cache_dir, target_pin, gf.localization.divergent_path)

    # Facts recovered once per file and shared by every hunk of it.
    entity = Path(gf.display_name).stem
    shared = {
        "source_dependencies": _deps(cache_dir, source_pin, gf.source_path),
        "target_dependencies": _deps(cache_dir, target_pin, gf.localization.divergent_path),
        "covering_tests": _covering_tests(cache_dir, target_pin, entity),
        "target_entity": entity,
        "refactorings": refactorings,
    }

    for source in sources:
        fn_id = fn_ids[source.hunk_id]
        hunk_dir = f"hunks/{source.hunk_id}"
        if (diff := source.diff) is not None:
            sap.add_payload(f"{hunk_dir}/hunk.diff", diff)

        ctx = AnalysisContext(
            hunk_id=source.hunk_id,
            fn_id=fn_id,
            source_file=gf.source_path,
            ext=ext,
            source_before=source.before,
            source_after=source.after,
            diff=diff,
            target_path=gf.localization.divergent_path,
            source_repo=source_repo,
            target_repo=target_repo,
            source_pin=source_pin,
            target_pin=target_pin,
            hunk_index=source.index,
            hunk_count=len(sources),
            input_artifacts=_input_artifacts(gf, source.artifacts),
            gacpd_dir=str(gf.file_dir),
            source_file_text=source_file_text,
            target_file_text=target_file_text,
            extras={
                "context_files": context_names,
                "hunk_order": hunk_order,
                "alignment_confidence": gf.localization.confidence(source.hunk_id),
                "similarity_breakdown": gf.localization.breakdown(source.hunk_id),
                "candidates": [],
                "edit_region": source.header.spans() if source.header else None,
                "region_diagnostic": _REGION_DIAGNOSTIC,
                "derivation_note": source.derivation_note,
                **shared,
            },
        )

        categories = {}
        for analyzer in analyzers:
            try:
                ce = analyzer.investigate(ctx)
            except Exception as exc:  # noqa: BLE001 - one analyzer must not abort the build
                log.warning(
                    "analyzer %s failed on %s: %s", analyzer.component_name, source.hunk_id, exc
                )
                ce = analyzer.unavailable(ctx, f"analyzer error: {exc}")
            categories[analyzer.category.value] = ce
            # An investigation may contribute payloads its elements reference.
            sap.payloads.update(ce.payloads)

        sap.hunks.append(
            Hunk(
                hunk_id=source.hunk_id,
                transformation=TransformationUnit(
                    fn_id=fn_id, edit_regions=[f"{source.hunk_id}:ER-1"]
                ),
                categories=categories,
                relationships=_relationships(source.hunk_id, fn_id),
            )
        )

    sap.hunk_order = hunk_order
    return sap
