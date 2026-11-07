"""Locating an edit region's context in a parsed file.

Every entry point takes the file's extension and resolves a grammar from it. A
file whose language has no installed grammar yields an EditContext that is not
``found``, carrying a diagnostic naming the missing package -- never a context
parsed with some other language's grammar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from salp.models import ContextType
from salp.structural.grammars import Grammar, diagnostic_for, grammar_for
from salp.structural.syntax import (
    control_flow_of,
    enclosing_class,
    enclosing_method,
    is_import_region,
    method_name,
    method_signature,
    node_lines,
    node_text,
    parse,
    query,
    sort_by_position,
    whole_lines,
)


@dataclass
class EditContext:
    """The program structure surrounding one edit region."""

    found: bool = False
    is_import_region: bool = False
    package: str | None = None
    imports: list[str] = field(default_factory=list)
    class_name: str | None = None
    class_source: str | None = None
    imports_source: str | None = None
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
    _node: Any = None # The tree sitter node that represents the common descendent of the region
    _grammar: Grammar | None = None
    context_type: ContextType = ContextType.WHOLE_FILE # Determines the type of the source artifact

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


def imports_of(tree: Any, source: str, grammar: Grammar | None, 
               remove_semi_colon: bool = True) -> list[str]:
    """Every import declaration in the file, in source order."""
    if grammar is None:
        return []
    captures = query(
        tree.root_node if tree else None, f"({grammar.import_declaration}) @i", grammar
    )
    return [
        (node_text(n, source).strip().rstrip(";")
         if remove_semi_colon else node_text(n, source).strip())
        for n in sort_by_position(captures.get("i", []))
    ]


def import_block(
    tree: Any, source: str, grammar: Grammar | None, lines: tuple[int, int] | None = None
) -> str | None:
    """A file's import block, verbatim, as it would be found in the file.

    The whole lines from the first import of a run to its last, so the blank
    lines and comments between import groups are kept: a consumer that locates
    the block by its text needs every byte. Rebuilding it from the import nodes
    drops those lines, and the result then occurs nowhere in the file.

    Only file-level imports form the block. Scala also allows imports inside a
    class or object; a slice reaching those would take the whole body with it.
    A file can hold several runs -- Scala lets imports follow later package
    clauses -- so ``lines``, a 0-based inclusive range, picks the run it touches,
    and the first run is used when none does.
    """
    if tree is None or grammar is None:
        return None
    runs: list[list[Any]] = []
    run: list[Any] = []
    for child in tree.root_node.named_children:
        if child.type == grammar.import_declaration:
            run.append(child)
        elif child.type not in grammar.comment and run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    if not runs:
        return None
    chosen = runs[0]
    if lines is not None:
        first, last = lines
        chosen = next(
            (r for r in runs if r[0].start_point[0] <= last and first <= r[-1].end_point[0]),
            chosen,
        )
    return whole_lines(source, chosen[0].start_byte, chosen[-1].end_byte)


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
        ctx.context_type = ContextType.IMPORTS_BLOCK
        ctx.imports_source = import_block(tree, source, grammar, (first, last))
        return ctx

    node = tree.root_node.named_descendant_for_point_range((first, 0), (last, 0))
    if node is None:
        ctx.diagnostics = "no named node covers the edit region"
        return ctx
    ctx._node = node
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

    if method is not None:
        ctx._method_node = method
        ctx.method_name = method_name(method, source)
        ctx.method_signature = method_signature(method, source, grammar)
        ctx.method_span = (method.start_point[0] + 1, method.end_point[0] + 1)
        ctx.method_source = node_text(method, source)
        ctx.context_type = ContextType.METHOD
    else:
        ctx.diagnostics = "edit region lies outside any method"

    # Fallback: imports block -> method -> class -> whole file
    klass = enclosing_class(node if method is None else method, grammar)
    if klass is not None:
        name = klass.child_by_field_name("name")
        ctx.class_name = node_text(name, source) if name is not None else None
        ctx.class_source = node_lines(klass, source)
        if method is None:
            ctx.context_type = ContextType.CLASS

    if method is None and klass is None:
        ctx.context_type = ContextType.WHOLE_FILE

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
    source: str | None, signature: str | None, name: str | None, ext: str | None
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
    if ext is None:
        return EditContext(diagnostics="There was no extension for file")
    grammar = grammar_for(ext)
    if grammar is None:
        return EditContext(diagnostics=diagnostic_for(ext))
    if source is None:
            return EditContext(diagnostics='There is no source file, make\
                                             sure to run "salp fetch-repos"')
    tree = parse(source, grammar)
    if tree is None:
        return EditContext(diagnostics="file is empty or could not be parsed")
    if not (signature or name):
        return EditContext(diagnostics="no source signature to match against", _tree = tree)

    captures = query(tree.root_node, f"{_any_of(grammar.method_like)} @method", grammar)
    methods = sort_by_position(captures.get("method", []))

    wanted_name = name or ""
    wanted_arity = (_parameter_count(signature) if signature is not None else None)
    by_signature = ([m for m in methods if method_signature(m, source, grammar) == signature] 
                    if signature is not None else None)
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
            # "found" is still not being set to true becuse we didn't find the method
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

def retrieve_class(node: Any, grammar: Grammar | None) -> str | None:
    """This function is used to discover the source side enclosing class of a node.
    This can be usefull for name-matching to find the corresponding class of the target
    file, when the target file's method cannot be found. 
    """
    if node and grammar:
        current = node
        while current is not None:
            if current.type in grammar.class_like:
                name_node = current.child_by_field_name('name')
                if name_node and name_node.text:
                    return str(name_node.text.decode('utf-8'))
                return None
                
            current = current.parent
            
    return None

def find_class_in_target(class_name: str | None, 
                         target_whole_file: str | None, 
                         grammar: Grammar | None) -> EditContext | None:
    """This function takes in a name string for a class, and searches all the classes
    in the target file to find the corresponding class in the target file, and returns
    the string of the class or none if not found.
    """
    if class_name and target_whole_file and grammar:
        tree = parse(target_whole_file, grammar)
        if not tree:
            return None
            
        def traverse(node: Any) -> EditContext | None:
            if node.type in grammar.class_like:
                name_node = node.child_by_field_name('name')
                if name_node and name_node.text and name_node.text.decode("utf-8") == class_name:
                    return EditContext(found = True,
                                        class_name = class_name,
                                        class_source = node_lines(node, target_whole_file),
                                        _tree = tree,
                                        _node = node,
                                        _grammar = grammar,
                                        context_type = ContextType.CLASS)
            for child in node.children:
                result = traverse(child)
                if result is not None:
                    return result
            return None

        return traverse(tree.root_node)

    return None

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
