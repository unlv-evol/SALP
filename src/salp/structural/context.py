"""Locating an edit region's context in a parsed file."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from salp.structural.java import (
    IMPORT_ERROR,
    TREE_SITTER_AVAILABLE,
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
    diagnostics: str | None = None
    _method_node: Any = None
    _tree: Any = None

    @property
    def has_method(self) -> bool:
        return self._method_node is not None


def _package_of(tree: Any, source: str) -> str | None:
    for child in tree.root_node.children:
        if child.type == "package_declaration":
            for part in child.children:
                if part.type == "scoped_identifier":
                    return node_text(part, source).rstrip(";").strip()
    return None


def package_of(source: str) -> str | None:
    """The package a file declares, or None."""
    tree = parse(source)
    return _package_of(tree, source) if tree is not None else None


def imports_of(tree: Any, source: str) -> list[str]:
    """Every import declaration in the file, in source order."""
    captures = query(tree.root_node if tree else None, "(import_declaration) @i")
    return [
        node_text(n, source).strip().rstrip(";")
        for n in sort_by_position(captures.get("i", []))
    ]


def imported_class_names(tree: Any, source: str) -> list[str]:
    """The simple class name of each import, e.g. ``Objects`` from ``util.Objects``."""
    names = []
    for statement in imports_of(tree, source):
        tail = statement.split(".")[-1].strip()
        if tail and tail != "*":
            names.append(tail)
    return names


def methods_overlapping(tree: Any, source: str, start_line: int, end_line: int) -> list[Any]:
    """Every method whose line range intersects a 1-based inclusive edit region.

    Adopted from ``get_methods_in_hunk`` in the reference implementation. Two
    ranges intersect when ``max(starts) <= min(ends)``. Tree-sitter points are
    0-based and diff line numbers are 1-based, so node rows are shifted by one
    before comparing.
    """
    captures = query(
        tree.root_node if tree else None,
        "[(method_declaration) (constructor_declaration)] @method",
    )
    return [
        node
        for node in sort_by_position(captures.get("method", []))
        if max(node.start_point[0] + 1, start_line) <= min(node.end_point[0] + 1, end_line)
    ]


def locate(source: str, start_line: int, end_line: int) -> EditContext:
    """Find the context enclosing a 1-based inclusive line range.

    Line numbers come from the unified-diff header, which is 1-based; tree-sitter
    points are 0-based, so the range is converted on the way in.
    """
    if not TREE_SITTER_AVAILABLE:
        return EditContext(diagnostics=IMPORT_ERROR or "tree-sitter unavailable")
    tree = parse(source)
    if tree is None:
        return EditContext(diagnostics="file is empty or could not be parsed")

    first = max(start_line - 1, 0)
    last = max(end_line - 1, first)
    ctx = EditContext(
        found=True,
        _tree=tree,
        package=_package_of(tree, source),
        imports=imports_of(tree, source),
    )

    if is_import_region(source, first, last):
        ctx.is_import_region = True
        return ctx

    node = tree.root_node.named_descendant_for_point_range((first, 0), (last, 0))
    if node is None:
        ctx.diagnostics = "no named node covers the edit region"
        return ctx

    # An edit region that crosses a method boundary has no single enclosing
    # method, so the smallest covering node is the class body. Selecting the
    # methods the region *overlaps* recovers what containment cannot.
    overlapping = methods_overlapping(tree, source, start_line, end_line)
    method = enclosing_method(node)
    if method is None and overlapping:
        method = overlapping[0]
        if len(overlapping) > 1:
            ctx.diagnostics = (
                f"edit region spans {len(overlapping)} methods; "
                "reporting the first and listing the rest"
            )
    ctx.overlapping_methods = [
        method_signature(m, source) for m in overlapping
    ]
    klass = enclosing_class(node if method is None else method)
    if klass is not None:
        name = klass.child_by_field_name("name")
        ctx.class_name = node_text(name, source) if name is not None else None

    if method is not None:
        ctx._method_node = method
        ctx.method_name = method_name(method, source)
        ctx.method_signature = method_signature(method, source)
        ctx.method_span = (method.start_point[0] + 1, method.end_point[0] + 1)
        ctx.method_source = node_text(method, source)
    else:
        ctx.diagnostics = "edit region lies outside any method"

    construct = control_flow_of(node) or control_flow_of(node.parent)
    ctx.immediate_construct = construct.value if construct else None
    return ctx


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
