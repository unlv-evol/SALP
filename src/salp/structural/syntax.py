"""Node navigation over a parsed file, written against the grammar vocabulary.

Nothing here names a node type directly: every language-specific name comes from
the ``Grammar`` passed in. That is deliberate -- the pipeline once parsed Scala
with the Java grammar and reported the result as evidence, and a function that
cannot see a node type cannot pick the wrong one.
"""

from __future__ import annotations

from typing import Any

from salp.structural.grammars import (
    IMPORT_ERROR,
    TREE_SITTER_AVAILABLE,
    ControlFlow,
    Grammar,
    parse,
    query,
)

__all__ = [
    "IMPORT_ERROR",
    "TREE_SITTER_AVAILABLE",
    "ControlFlow",
    "Grammar",
    "control_flow_of",
    "enclosing_class",
    "enclosing_method",
    "is_import_region",
    "is_in_method",
    "method_name",
    "method_signature",
    "node_text",
    "parameter_type",
    "parse",
    "query",
    "sort_by_position",
]


def node_text(node: Any, source: str) -> str:
    """The exact source text of a node.

    Slices bytes rather than lines and columns: tree-sitter end points are
    exclusive, and the line-based form the original used over-read by one
    character on every node.
    """
    if node is None:
        return ""
    return source.encode("utf-8")[node.start_byte : node.end_byte].decode("utf-8", "replace")


def sort_by_position(nodes: list[Any]) -> list[Any]:
    """Sort nodes into a *total* order.

    Nested constructs share a start point -- ``a.b().c()`` captures the outer and
    inner invocation at the same position -- so sorting on that alone leaves ties
    to be broken by capture order, which is not stable across runs. Including the
    end byte makes the order total, and the output reproducible.
    """
    return sorted(nodes or [], key=lambda n: (n.start_byte, n.end_byte))


# --- ancestry ------------------------------------------------------------------
def _ancestor(node: Any, wanted: frozenset[str], root: str) -> Any | None:
    """Walk up to the nearest ancestor of a wanted type, stopping at a boundary."""
    while node is not None and node.type not in wanted:
        if node.type == root:
            return None
        node = node.parent
    return node


def enclosing_method(node: Any, grammar: Grammar) -> Any | None:
    """The method, function, or constructor containing a node, if any."""
    found = _ancestor(node, grammar.boundary, grammar.root)
    return found if found is not None and found.type in grammar.method_like else None


def enclosing_class(node: Any, grammar: Grammar) -> Any | None:
    """The class-like declaration containing a node, if any."""
    while node is not None and node.type not in grammar.class_like:
        if node.type == grammar.root:
            return None
        node = node.parent
    return node


def is_in_method(node: Any, grammar: Grammar) -> bool:
    return enclosing_method(node, grammar) is not None


# --- signatures ----------------------------------------------------------------
def parameter_type(parameter: Any, source: str, grammar: Grammar) -> str:
    """The declared type of a formal parameter."""
    if parameter is None or parameter.type not in grammar.parameter:
        return ""
    for child in parameter.children:
        if "type" in child.type.lower():
            return node_text(child, source).strip()
    return ""


def method_signature(method: Any, source: str, grammar: Grammar) -> str:
    """A normalized signature: modifiers, return type, name, and parameter types.

    Parameter *types* rather than names, so the signature is stable across a
    rename of a parameter and can be compared between variants.

    Accumulation stops at the node the grammar marks as its body. Stopping at a
    block instead would serve Java but not Scala, where a body is any
    expression -- ``def size: Int = 1`` has an integer literal for a body, and
    the literal would be read into the signature.
    """
    if method is None or method.type not in grammar.method_like:
        return ""

    body = method.child_by_field_name("body")
    body_id = body.id if body is not None else None

    parts: list[str] = []
    for child in method.children:
        if child.id == body_id or child.type == grammar.block:
            break
        if child.type in grammar.parameters:
            types = [
                parameter_type(p, source, grammar)
                for p in child.children
                if p.type in grammar.parameter
            ]
            parts.append(f"({', '.join(t for t in types if t)})")
            continue
        parts.append(node_text(child, source))

    signature = " ".join(p for p in parts if p)
    return " ".join(signature.split()).replace(" (", "(")


def method_name(method: Any, source: str) -> str:
    if method is None:
        return ""
    name = method.child_by_field_name("name")
    return node_text(name, source) if name is not None else ""


# --- classification ------------------------------------------------------------
def control_flow_of(node: Any, grammar: Grammar) -> ControlFlow | None:
    """The control-flow construct a node represents, if it is one.

    ``if`` and ``else`` need their previous sibling to be disambiguated: an
    ``if`` preceded by ``else`` is an else-if, and a bare block preceded by
    ``else`` is an else branch. Both Java and Scala keep ``else`` as an anonymous
    sibling token, so one test serves.
    """
    if node is None:
        return None
    previous = node.prev_sibling
    preceded_by_else = previous is not None and previous.type == grammar.else_token
    if node.type == grammar.if_node:
        return ControlFlow.ELSE_IF if preceded_by_else else ControlFlow.IF
    if node.type == grammar.block and preceded_by_else:
        return ControlFlow.ELSE
    return grammar.control_flow.get(node.type)


def is_import_region(source: str, start_line: int, end_line: int, grammar: Grammar) -> bool:
    """Whether a line range contains only package/import declarations.

    An edit there sits outside any method or class, so it is described by its
    imports rather than by an enclosing function.
    """
    lines = source.splitlines()[start_line : end_line + 1]
    seen = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", "/")):
            continue
        if not stripped.startswith(grammar.import_prefixes):
            return False
        seen = True
    return seen
