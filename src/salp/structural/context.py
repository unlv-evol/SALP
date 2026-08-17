"""Locating an edit region's context in a parsed file.

Every entry point takes the file's extension and resolves a grammar from it. A
file whose language has no installed grammar yields an EditContext that is not
``found``, carrying a diagnostic naming the missing package -- never a context
parsed with some other language's grammar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from salp.structural.grammars import Grammar, diagnostic_for, grammar_for
from salp.structural.syntax import (
    control_flow_of,
    enclosing_class,
    enclosing_method,
    is_import_region,
    method_name,
    method_signature,
    node_text,
    parse,
    query,
    sort_by_position,
)


@dataclass
class EditContext:
    """The program structure surrounding one edit region."""

    found: bool = False
    is_import_region: bool = False
    package: str | None = None
    imports: list[str] = field(default_factory=list)
    class_name: str | None = None
    method_name: str | None = None
    method_signature: str | None = None
    method_span: tuple[int, int] | None = None
    immediate_construct: str | None = None
    method_source: str | None = None
    # Every method the edit region touches, not just the one enclosing it.
    overlapping_methods: list[str] = field(default_factory=list)
    # How this context was matched, when it was found by correspondence rather
    # than by containing a known line range. None means it was located directly.
    match_kind: str | None = None
    diagnostics: str | None = None
    _method_node: Any = None
    _tree: Any = None
    _grammar: Grammar | None = None

    @property
    def has_method(self) -> bool:
        return self._method_node is not None


def _any_of(node_types: frozenset[str]) -> str:
    """A tree-sitter alternation pattern over a set of node types."""
    return "[" + " ".join(f"({t})" for t in sorted(node_types)) + "]"


def _package_of(tree: Any, source: str, grammar: Grammar) -> str | None:
    for child in tree.root_node.children:
        if child.type != grammar.package:
            continue
        # Most specific first: a dotted name where the grammar has one, a bare
        # identifier for a single-segment package.
        for wanted in grammar.package_name:
            for part in child.children:
                if part.type == wanted:
                    return node_text(part, source).rstrip(";").strip()
    return None


def package_of(source: str, ext: str) -> str | None:
    """The package a file declares, or None."""
    grammar = grammar_for(ext)
    tree = parse(source, grammar)
    return _package_of(tree, source, grammar) if tree is not None and grammar else None


def imports_of(tree: Any, source: str, grammar: Grammar | None) -> list[str]:
    """Every import declaration in the file, in source order."""
    if grammar is None:
        return []
    captures = query(
        tree.root_node if tree else None, f"({grammar.import_declaration}) @i", grammar
    )
    return [
        node_text(n, source).strip().rstrip(";")
        for n in sort_by_position(captures.get("i", []))
    ]


def imported_class_names(tree: Any, source: str, grammar: Grammar | None) -> list[str]:
    """The simple class name of each import, e.g. ``Objects`` from ``util.Objects``.

    Scala can import several names at once -- ``import a.b.{C, D}`` -- so the
    tail is split on the selector braces rather than assumed to be one name.
    """
    names = []
    for statement in imports_of(tree, source, grammar):
        tail = statement.split(".")[-1].strip()
        for name in tail.strip("{}").split(","):
            cleaned = name.split("=>")[-1].strip()
            if cleaned and cleaned not in {"*", "_"}:
                names.append(cleaned)
    return names


def methods_overlapping(
    tree: Any, source: str, start_line: int, end_line: int, grammar: Grammar | None
) -> list[Any]:
    """Every method whose line range intersects a 1-based inclusive edit region.

    Adopted from ``get_methods_in_hunk`` in the reference implementation. Two
    ranges intersect when ``max(starts) <= min(ends)``. Tree-sitter points are
    0-based and diff line numbers are 1-based, so node rows are shifted by one
    before comparing.
    """
    if grammar is None:
        return []
    captures = query(
        tree.root_node if tree else None,
        f"{_any_of(grammar.method_like)} @method",
        grammar,
    )
    return [
        node
        for node in sort_by_position(captures.get("method", []))
        if max(node.start_point[0] + 1, start_line) <= min(node.end_point[0] + 1, end_line)
    ]


def locate(source: str, start_line: int, end_line: int, ext: str) -> EditContext:
    """Find the context enclosing a 1-based inclusive line range.

    Line numbers come from the unified-diff header, which is 1-based; tree-sitter
    points are 0-based, so the range is converted on the way in.
    """
    grammar = grammar_for(ext)
    if grammar is None:
        return EditContext(diagnostics=diagnostic_for(ext))
    tree = parse(source, grammar)
    if tree is None:
        return EditContext(diagnostics="file is empty or could not be parsed")

    first = max(start_line - 1, 0)
    last = max(end_line - 1, first)
    ctx = EditContext(
        found=True,
        _tree=tree,
        _grammar=grammar,
        package=_package_of(tree, source, grammar),
        imports=imports_of(tree, source, grammar),
    )

    if is_import_region(source, first, last, grammar):
        ctx.is_import_region = True
        return ctx

    node = tree.root_node.named_descendant_for_point_range((first, 0), (last, 0))
    if node is None:
        ctx.diagnostics = "no named node covers the edit region"
        return ctx

    # An edit region that crosses a method boundary has no single enclosing
    # method, so the smallest covering node is the class body. Selecting the
    # methods the region *overlaps* recovers what containment cannot.
    overlapping = methods_overlapping(tree, source, start_line, end_line, grammar)
    method = enclosing_method(node, grammar)
    if method is None and overlapping:
        method = overlapping[0]
        if len(overlapping) > 1:
            ctx.diagnostics = (
                f"edit region spans {len(overlapping)} methods; "
                "reporting the first and listing the rest"
            )
    ctx.overlapping_methods = [
        method_signature(m, source, grammar) for m in overlapping
    ]
    klass = enclosing_class(node if method is None else method, grammar)
    if klass is not None:
        name = klass.child_by_field_name("name")
        ctx.class_name = node_text(name, source) if name is not None else None

    if method is not None:
        ctx._method_node = method
        ctx.method_name = method_name(method, source)
        ctx.method_signature = method_signature(method, source, grammar)
        ctx.method_span = (method.start_point[0] + 1, method.end_point[0] + 1)
        ctx.method_source = node_text(method, source)
    else:
        ctx.diagnostics = "edit region lies outside any method"

    construct = control_flow_of(node, grammar) or control_flow_of(node.parent, grammar)
    ctx.immediate_construct = construct.value if construct else None
    return ctx


def _parameter_count(signature: str) -> int:
    """How many parameters a normalized signature declares."""
    inner = signature.partition("(")[2].rpartition(")")[0].strip()
    return len([p for p in inner.split(",") if p.strip()]) if inner else 0


# How a method in one variant was matched to a method in the other, weakest last.
# The caller reports this, so a weaker match is visible rather than passed off as
# an exact one.
MATCH_EXACT = "signature"
MATCH_NAME_ARITY = "name_and_arity"
MATCH_NAME = "name"


def file_context(source: str, ext: str) -> EditContext:
    """File-level structure only: package and imports, no method.

    An edit region in the import block has no enclosing method to correspond to,
    but the file it lives in still has recoverable structure. This is what the
    counterpart of such a region looks like on the other side.
    """
    grammar = grammar_for(ext)
    if grammar is None:
        return EditContext(diagnostics=diagnostic_for(ext))
    tree = parse(source, grammar)
    if tree is None:
        return EditContext(diagnostics="file is empty or could not be parsed")
    return EditContext(
        found=True,
        _tree=tree,
        _grammar=grammar,
        package=_package_of(tree, source, grammar),
        imports=imports_of(tree, source, grammar),
        diagnostics="file-level context only; the edit region encloses no method",
    )


def locate_method(
    source: str, signature: str, name: str | None, ext: str
) -> EditContext:
    """Find a method by *correspondence*, not by line number.

    A diverged variant has drifted in both content and position, so the source's
    line span says nothing about where its counterpart lives in the target. The
    signature does. Matching degrades in three steps -- exact signature, then
    name and arity, then name alone -- and records which one succeeded, because a
    name-only match is a weaker claim than an exact one and must not be reported
    as if it were the same thing.

    An overload resolved by name alone is genuinely ambiguous; the first in file
    order is returned and the ambiguity is recorded in ``diagnostics``.
    """
    grammar = grammar_for(ext)
    if grammar is None:
        return EditContext(diagnostics=diagnostic_for(ext))
    tree = parse(source, grammar)
    if tree is None:
        return EditContext(diagnostics="file is empty or could not be parsed")
    if not signature and not name:
        return EditContext(diagnostics="no source signature to match against")

    captures = query(tree.root_node, f"{_any_of(grammar.method_like)} @method", grammar)
    methods = sort_by_position(captures.get("method", []))

    wanted_name = name or ""
    wanted_arity = _parameter_count(signature)
    by_signature = [m for m in methods if method_signature(m, source, grammar) == signature]
    by_name = [m for m in methods if method_name(m, source) == wanted_name]
    by_name_arity = [
        m for m in by_name
        if _parameter_count(method_signature(m, source, grammar)) == wanted_arity
    ]

    if by_signature:
        matched, how, note = by_signature[0], MATCH_EXACT, None
    elif by_name_arity:
        matched, how, note = by_name_arity[0], MATCH_NAME_ARITY, (
            "no exact signature match; matched on name and parameter count, so "
            "modifiers, return type, or parameter types differ between variants"
        )
    elif by_name:
        matched, how, note = by_name[0], MATCH_NAME, (
            f"no signature or arity match; matched on the name {wanted_name!r} alone"
        )
    else:
        return EditContext(
            found=True,
            _tree=tree,
            _grammar=grammar,
            package=_package_of(tree, source, grammar),
            imports=imports_of(tree, source, grammar),
            diagnostics=f"no method corresponding to {signature or wanted_name!r} in this file",
        )

    candidates = by_signature or by_name_arity or by_name
    if len(candidates) > 1 and how != MATCH_EXACT:
        extra = f"; {len(candidates)} candidates, taking the first in file order"
        note = (note or "") + extra

    klass = enclosing_class(matched, grammar)
    class_name = None
    if klass is not None:
        klass_name = klass.child_by_field_name("name")
        class_name = node_text(klass_name, source) if klass_name is not None else None

    return EditContext(
        found=True,
        _tree=tree,
        _grammar=grammar,
        _method_node=matched,
        package=_package_of(tree, source, grammar),
        imports=imports_of(tree, source, grammar),
        class_name=class_name,
        method_name=method_name(matched, source),
        method_signature=method_signature(matched, source, grammar),
        method_span=(matched.start_point[0] + 1, matched.end_point[0] + 1),
        method_source=node_text(matched, source),
        match_kind=how,
        diagnostics=note,
    )


def to_ast_dict(node: Any, source: str, *, include_text: bool = False) -> dict[str, object]:
    """A JSON-serializable AST, normalized away from parser internals.

    Text is omitted by default: the AST is index-side structure, and the program
    text it describes is already stored once as a payload.
    """
    if node is None:
        return {}
    entry: dict[str, object] = {
        "type": node.type,
        "start_point": list(node.start_point),
        "end_point": list(node.end_point),
    }
    if include_text:
        entry["text"] = node_text(node, source)
    entry["children"] = [to_ast_dict(c, source, include_text=include_text) for c in node.children]
    return entry
