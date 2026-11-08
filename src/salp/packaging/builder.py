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
    changed_spans,
    edited_sides,
    hunk_region,
    hunk_side,
    parse_hunk_header,
    revert_patch,
    split_patch,
)
from salp.models import (
    DEFAULT_SPECS,
    SAP,
    CategoryEvidence,
    ChangeType,
    ContextType,
    FunctionPayload,
    Hunk,
    Provenance,
    Relationship,
    RepositoryStatePin,
    TransformationUnit,
)
from salp.repos import grep_files, read_dependencies, read_file
from salp.structural import (
    EditContext,
    find_class_in_target,
    grammar_for,
    import_block,
    locate,
    locate_method,
    parse,
)

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

    def described_side(self) -> str:
        """The side whose lines say what this hunk changes.

        The post-change side, unless the hunk only deletes: then that side is
        just the point the lines left, and what encloses it is a neighbour.
        """
        deletes, adds = edited_sides(self.diff, header=self.header)
        return "before" if deletes and not adds else "after"

    def edit_span(self, side: str) -> tuple[int, int] | None:
        """The range to locate this hunk's enclosing function from.

        The changed lines where the diff yields them, the whole hunk span --
        context included -- where it does not.
        """
        before, after = changed_spans(self.diff, header=self.header)
        span = before if side == "before" else after
        if span is not None or self.header is None:
            return span
        h = self.header
        return (h.old_start, h.old_end) if side == "before" else (h.new_start, h.new_end)

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


@dataclass
class ContextContainer:
    """The three contexts one hunk's transformation is cut from."""

    src_before: EditContext
    src_after: EditContext
    target: EditContext
    # Whether the hunk changes lines on each side, or only marks a point there.
    edits_before: bool = True
    edits_after: bool = True
    # The source side whose method was found in the target, when one was.
    matched: EditContext | None = None

    @property
    def anchor(self) -> EditContext:
        """The source side a coarser target -- class, import block -- is found from.

        The pre-change side, which is the state the target diverged from, and
        the post-change side only when the pre-change file could not be rebuilt.
        """
        return self.src_before if self.src_before.found else self.src_after

    @property
    def method_sides(self) -> list[EditContext]:
        """The source methods to look for in the target, in the order to try them.

        The pre-change method first, being the state the target diverged from.
        But that method need not exist there: a change that restructures code
        -- a revert, a split, an extraction -- edits lines of a method the fork
        never had, while the method the lines end up in is one it does have.
        So the post-change method is tried next, before giving up on a method.

        A side counts only where the hunk changes lines on it. A pure deletion's
        post-change side, or a pure insertion's pre-change side, is just the
        point the lines left or arrived at, and the method beside that point is
        a neighbour of the change, not its subject.
        """
        candidates = ((self.src_before, self.edits_before), (self.src_after, self.edits_after))
        sides = [side for side, edited in candidates if edited and side.has_method]
        if len(sides) == 2 and sides[0].method_signature == sides[1].method_signature:
            return sides[:1]
        return sides

    @property
    def match_source(self) -> EditContext:
        """The source side the target corresponds to."""
        return self.matched or self.anchor

    @property
    def described(self) -> list[EditContext]:
        """The sides that say what the hunk changes, post-change first.

        Only sides the hunk changes lines on: the other side of a one-sided hunk
        is the point the lines left or arrived at, so naming an entry after what
        encloses it names a neighbour of the change.
        """
        candidates = ((self.src_after, self.edits_after), (self.src_before, self.edits_before))
        return [side for side, edited in candidates if edited] or [self.src_after, self.src_before]


def _enclosing_function(
    text: str | None, span: tuple[int, int] | None, ext: str
) -> EditContext:
    """The context around a hunk's edit region in a whole file, method or not."""
    if not text:
        return EditContext(diagnostics="no file at the pinned state; run `salp fetch-repos`")
    if span is None:
        return EditContext(diagnostics="could not determine the edit span")
    if grammar_for(ext) is None:
        return EditContext(diagnostics=NO_GRAMMAR)
    return locate(text, span[0], span[1], ext)


def _no_function_reason(files: _FileStates, ctx: EditContext, ext: str) -> str:
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
    if not ctx.found:
        return NO_PARSE
    return IMPORT_REGION if ctx.is_import_region else OUTSIDE_ANY_METHOD


def _target_context(
    ctxs: ContextContainer, files: _FileStates, ext: str
) -> tuple[EditContext, EditContext | None]:
    """The target-side counterpart of a hunk, at the finest level it exists.

    A method is matched by signature, trying each source side's method in turn,
    then a class by name, then an import block by the target's own block.
    Whatever has no counterpart at its own level falls back to the next coarser
    one, ending at the whole file, and the reason the finer match failed is
    kept in ``diagnostics``.

    Returns the target and the source side whose method it matched, if any.
    """
    grammar = grammar_for(ext)
    why: str | None = None

    for side in ctxs.method_sides if files.target else []:
        method = locate_method(files.target, side.method_signature or "", side.method_name, ext)
        if method.has_method:
            method.context_type = ContextType.METHOD
            return method, side
        why = why or method.diagnostics

    source = ctxs.anchor

    if source.context_type in (ContextType.METHOD, ContextType.CLASS):
        klass = find_class_in_target(source.class_name, files.target_whole, grammar)
        if klass is not None:
            klass.diagnostics = why
            return klass, None

    if source.context_type is ContextType.IMPORTS_BLOCK and files.target_whole:
        tree = parse(files.target_whole, grammar)
        if block := import_block(tree, files.target_whole, grammar):
            return EditContext(
                found=True, _tree=tree, _grammar=grammar, imports_source=block,
                context_type=ContextType.IMPORTS_BLOCK,
            ), None

    return EditContext(
        context_type=ContextType.WHOLE_FILE,
        diagnostics=why or "no finer counterpart in the target file",
    ), None


def _context_text(ctx: EditContext, text: str | None) -> str | None:
    """The program text a context stands for, cut from the file it was found in."""
    match ctx.context_type:
        case ContextType.METHOD:
            return ctx.method_source
        case ContextType.CLASS:
            return ctx.class_source
        case ContextType.IMPORTS_BLOCK:
            return ctx.imports_source
        case _:
            return text


def _context_name(ctx: EditContext) -> str | None:
    """What a function-pool entry built from this context is named after."""
    match ctx.context_type:
        case ContextType.METHOD:
            return ctx.method_name
        case ContextType.CLASS:
            return ctx.class_name
        case ContextType.IMPORTS_BLOCK:
            return "imports"
        case _:
            return None


def _locate_contexts(source: _HunkSource, files: _FileStates, ext: str) -> ContextContainer:
    """Locate one hunk's own source contexts and their target counterpart.

    Every hunk is located from its own edit span. Hunks reported under one diff
    section heading do not necessarily share an enclosing method -- the heading
    is often just the class -- so grouping before locating attributes some hunks
    to a method they are not in.
    """
    before = _enclosing_function(files.source_before, source.edit_span("before"), ext)
    after = _enclosing_function(files.source_after, source.edit_span("after"), ext)
    edits_before, edits_after = edited_sides(source.diff, header=source.header)
    ctxs = ContextContainer(before, after, EditContext(), edits_before, edits_after)
    ctxs.target, ctxs.matched = _target_context(ctxs, files, ext)
    return ctxs


def _build_function_pool(
    sap: SAP,
    sources: list[_HunkSource],
    file_stem: str,
    ext: str,
    files: _FileStates,
) -> dict[str, str]:
    """Register one pool entry per distinct transformation, and map hunks onto them.

    The transformation the specification asks for is ``tau = (f_s, f'_s, f_t)``:
    the source unit before and after the change, and the target unit before
    adaptation. None of the three is recoverable from the GACPD artifacts alone
    -- ``full_del``/``full_add`` are hunk regions with diff context, and
    ``cmp/<File>`` is a whole file -- so each is cut out of the file at its
    pinned repository state, at the finest level that exists: the enclosing
    method, else the import block or enclosing class, else the whole file.

    Each hunk is located on its own, but hunks resolving to the same three
    payloads share one entry, so the pool holds every distinct unit exactly
    once. Where a source file is unavailable the entry falls back to the GACPD
    region and records that it did, which the required-field rule then scores
    as partial.

    An edit region outside every method has no function to transform. That is
    recorded distinctly from a failure to find one, because the two mean
    opposite things: the first is a property of the change, the second a gap in
    the analysis.

    Returns the function-pool identifier of every hunk.
    """
    assigned: dict[str, str] = {}
    pooled: dict[tuple[str | None, str | None, str | None], str] = {}

    for source in sources:
        ctxs = _locate_contexts(source, files, ext)
        before = _context_text(ctxs.src_before, files.source_before)
        after = _context_text(ctxs.src_after, files.source_after)
        target = _context_text(ctxs.target, files.target_whole)

        # Keyed on the payloads themselves: two hunks share an entry exactly when
        # the entry would hold the same three files for both.
        key = (before or source.before, after or source.after, target)
        if (fn_id := pooled.get(key)) is not None:
            assigned[source.hunk_id] = fn_id
            continue

        name = next(filter(None, map(_context_name, ctxs.described)), None)
        fn_id = f"{file_stem}_{_slug(name)}" if name else f"{file_stem}_fn{source.index}"
        while fn_id in sap.functions:  # distinct units sharing a name stay distinct
            fn_id = f"{fn_id}_{source.index}"

        fn = FunctionPayload(
            fn_id=fn_id,
            ext=ext,
            signature=next((s.method_signature for s in ctxs.described if s.has_method), None),
            method_name=next((s.method_name for s in ctxs.described if s.has_method), None),
            match_signature=ctxs.match_source.method_signature,
            match_method_name=ctxs.match_source.method_name,
        )
        if not (ctxs.src_before.has_method or ctxs.src_after.has_method):
            fn.no_function_reason = _no_function_reason(
                files, ctxs.src_after if ctxs.src_after.found else ctxs.src_before, ext
            )

        if after is not None:
            sap.add_payload(fn.source_after_ref, after)
            fn.has_source_after = True
            fn.source_after_type = ctxs.src_after.context_type
        elif source.after is not None:
            sap.add_payload(fn.source_after_ref, source.after)
            fn.has_source_after = True
            fn.source_after_is_region = True

        if before is not None:
            sap.add_payload(fn.source_before_ref, before)
            fn.has_source_before = True
            fn.source_before_type = ctxs.src_before.context_type
        elif source.before is not None:
            sap.add_payload(fn.source_before_ref, source.before)
            fn.has_source_before = True
            fn.source_before_is_region = True

        if target is not None:
            sap.add_payload(fn.target_ref, target)
            fn.has_target = True
            fn.target_type = ctxs.target.context_type
            fn.target_is_whole_file = ctxs.target.context_type is ContextType.WHOLE_FILE
            fn.target_signature = ctxs.target.method_signature
            fn.target_match_kind = ctxs.target.match_kind
            fn.target_diagnostics = ctxs.target.diagnostics

        sap.functions[fn_id] = fn
        pooled[key] = fn_id
        assigned[source.hunk_id] = fn_id

    return assigned


def _file_lines(text: str | None, start: int, count: int) -> list[str] | None:
    """Lines ``start .. start + count - 1`` (1-based) of a file, terminators kept."""
    if text is None or count < 1 or start < 1:
        return None
    lines = text.splitlines(keepends=True)
    return lines[start - 1 : start - 1 + count] if start - 1 + count <= len(lines) else None


def _hunk_regions(
    source: _HunkSource, files: _FileStates
) -> tuple[str | None, str | None, str | None]:
    """Both sides of a hunk as the files hold them, and what they were cut from.

    Each side is the hunk's ``@@`` range -- its context lines included, so a
    consumer always has something to find, even before a pure insertion. It is
    cut from the whole file when both files agree with the diff line for line,
    keeping the file's own bytes, and rebuilt from the diff otherwise, so a
    file pinned to the wrong state cannot yield a plausible but foreign region.

    Returns ``(before, after, cut_from)``, with ``cut_from`` "file" or "diff".
    """
    h = source.header
    if h is None:
        return None, None, None
    from_diff = [hunk_region(source.diff, side=s, header=h) for s in ("before", "after")]
    from_file = [
        _file_lines(files.source_before, h.old_start, h.old_count),
        _file_lines(files.source_after, h.new_start, h.new_count),
    ]
    if all(
        lines is not None and rebuilt is not None
        and [line.rstrip("\r\n") for line in lines] == rebuilt.splitlines()
        for lines, rebuilt in zip(from_file, from_diff, strict=True)
    ):
        return "".join(from_file[0] or []), "".join(from_file[1] or []), "file"
    if any(side is not None for side in from_diff):
        return from_diff[0], from_diff[1], "diff"
    return None, None, None


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


def _relationships(
    hunk_id: str, 
    fn_id: str, 
    categories: dict[str, CategoryEvidence] | None = None
) -> list[Relationship]:
    """Typed edges from the edit region to objects reduction should reach.

    Reduction is a reachability computation rooted at the edit region, so an
    object that no edge reaches is never materialized into the adaptation
    context. The alignment edge is always present; enriched analyzers
    contribute additional edges (calls, uses, overrides, dependencies, etc.).
    """
    relationships: list[Relationship] = [
        Relationship(src=f"{hunk_id}:ER-1", rel="aligned_to", dst=f"functions/{fn_id}"),
    ]
    
    if not categories:
        return relationships
    
    # Extract inferred relationships from existing evidence
    
    # From target localization: if we located the corresponding target function
    if "target_localization" in categories:
        ce = categories["target_localization"]
        for elem in ce.elements:
            if elem.state.value == "PRESENT" and elem.attributes.get("target_function"):
                target_fn = elem.attributes["target_function"]
                relationships.append(
                    Relationship(
                        src=f"{hunk_id}:ER-1",
                        rel="located_in",
                        dst=f"functions/{target_fn}"
                    )
                )
    
    # From refactoring: if method was renamed or moved
    if "refactoring" in categories:
        ce = categories["refactoring"]
        for elem in ce.elements:
            if (elem.state.value == "PRESENT" and
                elem.attributes.get("operation_type") == "Rename Method"):
                    old_name = elem.attributes.get("old_name")
                    new_name = elem.attributes.get("new_name")
                    if old_name and new_name:
                        relationships.append(
                            Relationship(
                                src=f"{hunk_id}:ER-1",
                                rel="renamed_to",
                                dst=f"{old_name}→{new_name}"
                            )
                        )
    
    # From structural: if we identified class/method structure
    if "structural" in categories:
        ce = categories["structural"]
        for elem in ce.elements:
            if elem.state.value == "PRESENT" and elem.attributes.get("class"):
                class_name = elem.attributes["class"]
                relationships.append(
                    Relationship(
                        src=f"{hunk_id}:ER-1",
                        rel="within_class",
                        dst=f"class/{class_name}"
                    )
                )
    
    # Placeholder for future enriched analyzers (static call graph, data flow, etc.)
    # These would be populated once enriched analyzers are implemented
    # - calls: function calls from edit region
    # - uses: field/variable accesses
    # - overrides: method override relationships
    # - dependency: external dependencies
    
    return relationships


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

    files = _FileStates(
        source_after=source_file_text,
        source_before=revert_patch(source_file_text, _read(gf.patch)),
        target=target_file_text,
        target_whole=target_file_text or _read(gf.target_file),
    )
    fn_ids = _build_function_pool(sap, sources, file_stem, ext, files)

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
        region_before, region_after, region_cut_from = _hunk_regions(source, files)
        if region_before is not None:
            region_before = sap.add_payload(f"{hunk_dir}/region.before.{ext}", region_before)
        if region_after is not None:
            region_after = sap.add_payload(f"{hunk_dir}/region.after.{ext}", region_after)

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
            edit_span=source.edit_span(source.described_side()),
            edit_text=(
                files.source_before if source.described_side() == "before"
                else files.source_after
            ),
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
                    fn_id=fn_id, edit_regions=[f"{source.hunk_id}:ER-1"],
                    region_before=region_before, region_after=region_after,
                    region_cut_from=region_cut_from,
                ),
                categories=categories,
                relationships=_relationships(source.hunk_id, fn_id, categories),
                # condition 5: several plausible alignments with no supported
                # primary caps Readiness at Moderate
                localization_ambiguous=len(shared.get("candidates") or []) > 1,
            )
        )

    sap.hunk_order = hunk_order
    return sap
