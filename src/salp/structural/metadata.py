"""Structured metadata for the program context around an edit region."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from salp.structural.context import EditContext, imported_class_names
from salp.structural.java import (
    METHOD_LIKE,
    control_flow_of,
    enclosing_class,
    method_signature,
    node_text,
    query,
    sort_by_position,
)


@dataclass
class ClassStructure:
    """A class and its members, one level of nesting at a time."""

    name: str
    fields: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    nested_classes: list[ClassStructure] = field(default_factory=list)


@dataclass
class MethodRef:
    signature: str
    start_line: int
    end_line: int


@dataclass
class Invocation:
    text: str
    start_line: int
    end_line: int


@dataclass
class SurroundingMetadata:
    """Everything recovered about the program context of an edit region."""

    enclosing_class: ClassStructure | None = None
    package: str | None = None
    imports: list[str] = field(default_factory=list)
    invoked_methods: list[Invocation] = field(default_factory=list)
    referenced_classes: list[str] = field(default_factory=list)
    previous_method: MethodRef | None = None
    next_method: MethodRef | None = None
    control_flow: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], {})}


def _is_direct_member(node: Any, body: Any) -> bool:
    """Whether a captured node is a direct child of this class body.

    tree-sitter returns a fresh wrapper object on every ``.parent`` access, so
    identity comparison always fails; nodes are compared by their stable ``id``.
    """
    parent = node.parent
    return parent is not None and parent.id == body.id


_CLASS_MEMBERS = """
(class_body [
    (field_declaration) @field
    (method_declaration) @method
    (class_declaration) @nested
])
"""


def class_structure(
    class_node: Any, source: str, *, include_nested: bool = True, _depth: int = 0
) -> ClassStructure | None:
    """Name, fields, method signatures, and nested classes of a class node.

    A tree-sitter query matches at every depth, so members whose parent is not
    this class body are skipped -- otherwise a deeply nested class would be
    reported once per enclosing level.
    """
    if class_node is None:
        return None
    name_node = class_node.child_by_field_name("name")
    structure = ClassStructure(
        name=node_text(name_node, source) if name_node is not None else ""
    )
    body = class_node.child_by_field_name("body")
    if body is None:
        return structure

    captures = query(body, _CLASS_MEMBERS)
    for node in sort_by_position(captures.get("field", [])):
        if _is_direct_member(node, body):
            structure.fields.append(node_text(node, source).strip())
    for node in sort_by_position(captures.get("method", [])):
        if _is_direct_member(node, body):
            structure.methods.append(method_signature(node, source))
    if include_nested and _depth < 3:
        for node in sort_by_position(captures.get("nested", [])):
            if not _is_direct_member(node, body):
                continue
            nested = class_structure(
                node, source, include_nested=include_nested, _depth=_depth + 1
            )
            if nested is not None:
                structure.nested_classes.append(nested)
    return structure


def invoked_methods(method_node: Any, source: str) -> list[Invocation]:
    """Every method invocation inside a method, in source order."""
    captures = query(method_node, "(method_invocation) @call")
    return [
        Invocation(
            text=node_text(n, source),
            start_line=n.start_point[0] + 1,
            end_line=n.end_point[0] + 1,
        )
        for n in sort_by_position(captures.get("call", []))
    ]


def referenced_classes(method_node: Any, source: str, tree: Any) -> list[str]:
    """Imported classes actually referenced inside a method.

    Intersecting identifiers with the file's imports is what keeps this to
    genuine external references rather than every identifier in scope.
    """
    if method_node is None:
        return []
    imported = set(imported_class_names(tree, source))
    if not imported:
        return []
    captures = query(
        method_node,
        "[(scoped_identifier) (type_identifier) (identifier)] @ref",
    )
    seen: list[str] = []
    for node in captures.get("ref", []):
        text = node_text(node, source).split(".")[-1].strip()
        if text in imported and text not in seen:
            seen.append(text)
    return sorted(seen)


def neighboring_methods(
    method_node: Any, source: str
) -> tuple[MethodRef | None, MethodRef | None]:
    """The methods declared immediately before and after this one."""
    if method_node is None:
        return None, None

    def scan(start: Any, forward: bool) -> MethodRef | None:
        sibling = start
        while sibling is not None:
            if sibling.type in METHOD_LIKE:
                return MethodRef(
                    signature=method_signature(sibling, source),
                    start_line=sibling.start_point[0] + 1,
                    end_line=sibling.end_point[0] + 1,
                )
            sibling = sibling.next_named_sibling if forward else sibling.prev_named_sibling
        return None

    return scan(method_node.prev_named_sibling, False), scan(method_node.next_named_sibling, True)


def control_flow_constructs(method_node: Any) -> list[str]:
    """The distinct control-flow constructs present in a method."""
    if method_node is None:
        return []
    found: list[str] = []
    stack = [method_node]
    while stack:
        node = stack.pop()
        flow = control_flow_of(node)
        if flow is not None and flow.value not in found:
            found.append(flow.value)
        stack.extend(node.children)
    return sorted(found)


def describe(ctx: EditContext, source: str) -> SurroundingMetadata:
    """Assemble the surrounding metadata for a located edit context."""
    meta = SurroundingMetadata(package=ctx.package, imports=list(ctx.imports))
    if not ctx.found or ctx._method_node is None:
        return meta

    method = ctx._method_node
    enclosing = enclosing_class(method)
    meta.enclosing_class = class_structure(enclosing, source)
    meta.invoked_methods = invoked_methods(method, source)
    meta.referenced_classes = referenced_classes(method, source, ctx._tree)
    meta.previous_method, meta.next_method = neighboring_methods(method, source)
    meta.control_flow = control_flow_constructs(method)
    return meta
