"""SAP construction from a GACPD MO file."""

from __future__ import annotations

import re
from dataclasses import dataclass
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
    revert_patch,
    split_patch,
)
from salp.models import (
    DEFAULT_SPECS,
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
from salp.structural import grammar_for, locate, locate_method

log = get_logger(__name__)


# Why a hunk has no enclosing function. The first two are properties of the
# change -- there is no function to transform -- and the last three are gaps in
# the analysis. `FunctionPayload.has_no_function_by_construction` reads them.
IMPORT_REGION = "import_region"
OUTSIDE_ANY_METHOD = "outside_any_method"
NO_GRAMMAR = "no_grammar"
NO_PINNED_FILE = "no_pinned_file"
NO_PARSE = "no_parse"

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


def _with_dependencies(
    pin: RepositoryStatePin | None, deps: list[str] | None
) -> RepositoryStatePin | None:
    """Attach resolved dependency versions to a pin.

    Coordinates arrive as ``group:artifact:version``; the pin records them keyed
    by coordinate so a later run can tell whether the dependency set of a bound
    state has moved. A coordinate without a version contributes an empty string
    rather than being dropped -- that it was declared is itself the fact.
    """
    if pin is None or not deps:
        return pin
    versions: dict[str, str] = {}
    for coordinate in deps:
        group, _, version = coordinate.rpartition(":")
        versions[group or coordinate] = version if group else ""
    return pin.model_copy(update={"dependency_versions": versions})


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


def _region_tests(
    cache_dir: Path | None, pin: RepositoryStatePin | None, sources: list[_HunkSource]
) -> list[str] | None:
    """Target tests naming an edited method, not merely its enclosing type.

    A test that names the method exercises the edit region itself, which is what
    separates full coverage from partial.
    """
    if cache_dir is None or pin is None or not pin.is_resolved:
        return None
    names = {
        s.header.declared_name() for s in sources if s.header and s.header.declared_name()
    }
    found: list[str] = []
    for name in sorted(n for n in names if n):
        found += grep_files(cache_dir, pin, f"{name}(", "*Test*.java", "*Tests.java")
    return sorted(set(found))


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


@dataclass(frozen=True)
class _FileStates:
    """The whole-file text at each state the transformation is sliced from."""

    # The pinned source file sits at the pull-request head: the post-change state.
    source_after: str | None = None
    # The same file with the patch undone: the pre-change state.
    source_before: str | None = None
    # The pinned target file, from which the corresponding function is taken.
    target: str | None = None
    # Whole-file fallback when the corresponding function cannot be isolated.
    target_whole: str | None = None


def _enclosing_function(
    text: str | None, header: HunkHeader | None, side: str, ext: str
) -> Any | None:
    """The context around a hunk's edit region in a whole file, method or not."""
    if not text or header is None or grammar_for(ext) is None:
        return None
    start, end = (
        (header.old_start, header.old_end) if side == "before"
        else (header.new_start, header.new_end)
    )
    return locate(text, start, end, ext)


def _no_function_reason(files: _FileStates, ctx: Any | None, ext: str) -> str:
    """Why no enclosing function was found, distinguishing cannot-have from could-not-get.

    Two outcomes look alike and mean opposite things. A file that *parsed*, whose
    edit region provably sits outside every method -- an import block, a class
    parameter list, a field initialiser -- has no function to transform: that is
    a property of the change, and the caller records it NOT_APPLICABLE so it
    leaves both denominators. A file that could not be read or parsed might have
    a function that simply was not reached, which is a recoverable shortfall and
    stays UNAVAILABLE with the fix named.
    """
    if grammar_for(ext) is None:
        return NO_GRAMMAR
    if files.source_after is None:
        return NO_PINNED_FILE
    if ctx is None or not ctx.found:
        return NO_PARSE
    return IMPORT_REGION if ctx.is_import_region else OUTSIDE_ANY_METHOD


def _build_function_pool(
    sap: SAP,
    sources: list[_HunkSource],
    fn_ids: dict[str, str],
    ext: str,
    files: _FileStates,
) -> None:
    """Register one pool entry per distinct enclosing function.

    The transformation the specification asks for is ``tau = (f_s, f'_s, f_t)``,
    three *functions*: the source function before and after the change, and the
    target function before adaptation. None of the three is recoverable from the
    GACPD artifacts alone -- ``full_del``/``full_add`` are hunk regions with diff
    context, and ``cmp/<File>`` is a whole file -- so each is sliced out of the
    file at its pinned repository state:

    * ``f'_s`` is the method enclosing the edit region in the pinned source file,
      which sits at the pull-request head and is therefore the post-change state;
    * ``f_s`` is the same method in that file with the patch undone;
    * ``f_t`` is the *corresponding* method in the pinned target file, found by
      signature rather than by the source's line span.

    Where a whole file or a grammar is unavailable, the entry falls back to the
    GACPD region for the source sides and the whole file for the target, and
    records that it did. The fallback is what the required-field rule then scores
    as partial, so a degraded install stays usable without misreporting itself.

    An edit region in the import block has no enclosing function at all. That is
    recorded distinctly from a failure to find one, because the two mean opposite
    things: the first is a property of the change, the second a gap in the
    analysis.
    """
    grouped: dict[str, list[_HunkSource]] = {}
    for source in sources:
        grouped.setdefault(fn_ids[source.hunk_id], []).append(source)

    for fn_id, members in grouped.items():
        fn = FunctionPayload(fn_id=fn_id, ext=ext)
        header = next((m.header for m in members if m.header), None)

        after_ctx = _enclosing_function(files.source_after, header, "after", ext)
        before_ctx = _enclosing_function(files.source_before, header, "before", ext)
        if not ((after_ctx and after_ctx.has_method) or (before_ctx and before_ctx.has_method)):
            fn.no_function_reason = _no_function_reason(files, after_ctx or before_ctx, ext)

        if after_ctx is not None and after_ctx.method_source:
            sap.add_payload(fn.source_after_ref, after_ctx.method_source)
            fn.has_source_after = True
            fn.signature = after_ctx.method_signature
            fn.method_name = after_ctx.method_name
        elif regions := [m.after for m in members if m.after]:
            sap.add_payload(fn.source_after_ref, "\n".join(regions))
            fn.has_source_after = True
            fn.source_after_is_region = True

        if before_ctx is not None and before_ctx.method_source:
            sap.add_payload(fn.source_before_ref, before_ctx.method_source)
            fn.has_source_before = True
            fn.signature = fn.signature or before_ctx.method_signature
            fn.method_name = fn.method_name or before_ctx.method_name
        elif regions := [m.before for m in members if m.before]:
            sap.add_payload(fn.source_before_ref, "\n".join(regions))
            fn.has_source_before = True
            fn.source_before_is_region = True

        target_ctx = (
            locate_method(files.target, fn.signature or "", fn.method_name, ext)
            if files.target and fn.signature and grammar_for(ext) is not None
            else None
        )
        if target_ctx is not None and target_ctx.method_source:
            sap.add_payload(fn.target_ref, target_ctx.method_source)
            fn.has_target = True
            fn.target_signature = target_ctx.method_signature
            fn.target_match_kind = target_ctx.match_kind
        elif files.target_whole is not None:
            # The counterpart could not be isolated; the whole file is still the
            # best available evidence, but it is a file, not f_t.
            sap.add_payload(fn.target_ref, files.target_whole)
            fn.has_target = True
            fn.target_is_whole_file = True
            fn.target_diagnostics = (
                target_ctx.diagnostics if target_ctx is not None
                else "no pinned target file or no grammar; run `salp fetch-repos`"
            )
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
    refactorings: tuple[dict[str, Any], ...] | str | None = None,
    refactoringminer_jar: Path | None = None,
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

    # Whole files at the pinned states, for analyses that need the enclosing
    # structure GACPD's hunk regions cannot carry.
    source_file_text = _at_pin(cache_dir, source_pin, gf.source_path)
    target_file_text = _at_pin(cache_dir, target_pin, gf.localization.divergent_path)

    fn_ids = function_ids(sources, file_stem)
    _build_function_pool(sap, sources, fn_ids, ext, _FileStates(
        source_after=source_file_text,
        source_before=revert_patch(source_file_text, _read(gf.patch)),
        target=target_file_text,
        target_whole=target_file_text or _read(gf.target_file),
    ))

    context_names = [c.display_name for c in (pr.context_files if pr else [])]
    hunk_order = [s.hunk_id for s in sources]

    # §17: a pin records the resolved dependency versions of the state it binds.
    source_deps = _deps(cache_dir, source_pin, gf.source_path)
    target_deps = _deps(cache_dir, target_pin, gf.localization.divergent_path)
    source_pin = _with_dependencies(source_pin, source_deps)
    target_pin = _with_dependencies(target_pin, target_deps)

    # Facts recovered once per file and shared by every hunk of it.
    entity = Path(gf.display_name).stem
    shared = {
        "source_dependencies": source_deps,
        "target_dependencies": target_deps,
        "covering_tests": _covering_tests(cache_dir, target_pin, entity),
        "region_tests": _region_tests(cache_dir, target_pin, sources),
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
            change_type=sap.change_type,
            hunk_index=source.index,
            hunk_count=len(sources),
            input_artifacts=_input_artifacts(gf, source.artifacts),
            gacpd_dir=str(gf.file_dir),
            source_file_text=source_file_text,
            target_file_text=target_file_text,
            function=sap.functions.get(fn_id),
            extras={
                "context_files": context_names,
                "hunk_order": hunk_order,
                "alignment_confidence": gf.localization.confidence(source.hunk_id),
                "similarity_breakdown": gf.localization.breakdown(source.hunk_id),
                "candidates": [],
                "refactoringminer_jar": refactoringminer_jar,
                "edit_region": source.header.spans() if source.header else None,
                "region_diagnostic": _REGION_DIAGNOSTIC,
                "derivation_note": source.derivation_note,
                **shared,
            },
        )

        categories = {}
        for analyzer in analyzers:
            spec = DEFAULT_SPECS[analyzer.category]
            if ctx.change_type not in spec.applicable_to:
                # Gate centrally rather than in each analyzer: a category the
                # change type does not have is recorded NOT_APPLICABLE with a
                # reason, and leaves both characterization denominators.
                categories[analyzer.category.value] = analyzer.not_applicable(
                    ctx, f"not applicable to a {ctx.change_type.value} change"
                )
                continue
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
                # condition 5: several plausible alignments with no supported
                # primary caps Readiness at Moderate
                localization_ambiguous=len(shared.get("candidates") or []) > 1,
            )
        )

    sap.hunk_order = hunk_order
    return sap
