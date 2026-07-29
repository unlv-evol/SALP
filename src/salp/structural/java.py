"""Tree-sitter Java parsing and node navigation."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Any

# tree-sitter ships as the optional "structural" extra. An analyzer whose parser
# is unavailable records UNAVAILABLE with a diagnostic rather than failing.
try:  # pragma: no cover - import guard
    import tree_sitter_java as tsjava
    from tree_sitter import Language, Parser, Query, QueryCursor

    TREE_SITTER_AVAILABLE = True
    IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    TREE_SITTER_AVAILABLE = False
    IMPORT_ERROR = f"{exc}; install the 'structural' extra"


CLASS_LIKE = frozenset(
    {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
)


METHOD_LIKE = frozenset({"method_declaration", "constructor_declaration"})


_BOUNDARY = CLASS_LIKE | METHOD_LIKE | {"program"}


class ControlFlow(StrEnum):
    """Control-flow constructs recognised around an edit region."""

    IF = "if_statement"
    ELSE_IF = "else_if_statement"
    ELSE = "else_statement"
    FOR = "for_statement"
    ENHANCED_FOR = "enhanced_for_statement"
    WHILE = "while_statement"
    DO = "do_statement"
    SWITCH = "switch_expression"
    SWITCH_CASE = "switch_case"
    TRY = "try_statement"
    TRY_WITH_RESOURCES = "try_with_resources_statement"
    THROW = "throw_statement"
    RETURN = "return_statement"
    BREAK = "break_statement"
    CONTINUE = "continue_statement"
    ASSERT = "assert_statement"


_DIRECT_FLOW = {
    "for_statement": ControlFlow.FOR,
    "enhanced_for_statement": ControlFlow.ENHANCED_FOR,
    "while_statement": ControlFlow.WHILE,
    "do_statement": ControlFlow.DO,
    "switch_expression": ControlFlow.SWITCH,
    "switch_block_statement_group": ControlFlow.SWITCH_CASE,
    "try_statement": ControlFlow.TRY,
    "try_with_resources_statement": ControlFlow.TRY_WITH_RESOURCES,
    "throw_statement": ControlFlow.THROW,
    "return_statement": ControlFlow.RETURN,
    "break_statement": ControlFlow.BREAK,
    "continue_statement": ControlFlow.CONTINUE,
    "assert_statement": ControlFlow.ASSERT,
}


@lru_cache(maxsize=1)
def _language() -> Any:
    return Language(tsjava.language())


@lru_cache(maxsize=1)
def _parser() -> Any:
    return Parser(_language())


def parse(source: str) -> Any | None:
    """Parse Java source into a tree-sitter tree, or None if parsing is unavailable."""
    if not TREE_SITTER_AVAILABLE or not source:
        return None
    return _parser().parse(source.encode("utf-8"))


def query(node: Any, pattern: str) -> dict[str, list[Any]]:
    """Run a tree-sitter query over a subtree, returning its captures."""
    if node is None or not TREE_SITTER_AVAILABLE:
        return {}
    return QueryCursor(Query(_language(), pattern)).captures(node)


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
def _ancestor(node: Any, wanted: frozenset[str]) -> Any | None:
    """Walk up to the nearest ancestor of a wanted type, stopping at a boundary."""
    while node is not None and node.type not in wanted:
        if node.type == "program":
            return None
        node = node.parent
    return node


def enclosing_method(node: Any) -> Any | None:
    """The method or constructor containing a node, if any."""
    found = _ancestor(node, _BOUNDARY)
    return found if found is not None and found.type in METHOD_LIKE else None


def enclosing_class(node: Any) -> Any | None:
    """The class, interface, enum, or record containing a node, if any."""
    while node is not None and node.type not in CLASS_LIKE:
        if node.type == "program":
            return None
        node = node.parent
    return node


def is_in_method(node: Any) -> bool:
    return enclosing_method(node) is not None


# --- signatures ----------------------------------------------------------------
def parameter_type(parameter: Any, source: str) -> str:
    """The declared type of a formal parameter."""
    if parameter is None or parameter.type != "formal_parameter":
        return ""
    for child in parameter.children:
        if "type" in child.type.lower():
            return node_text(child, source).strip()
    return ""


def method_signature(method: Any, source: str) -> str:
    """A normalized signature: modifiers, return type, name, and parameter types.

    Parameter *types* rather than names, so the signature is stable across a
    rename of a parameter and can be compared between variants.
    """
    if method is None or method.type not in METHOD_LIKE:
        return ""

    parts: list[str] = []
    for child in method.children:
        if child.type == "formal_parameters":
            types = [
                parameter_type(p, source)
                for p in child.children
                if p.type == "formal_parameter"
            ]
            parts.append(f"({', '.join(t for t in types if t)})")
            continue
        if child.type == "block":
            break
        parts.append(node_text(child, source))

    signature = " ".join(p for p in parts if p)
    return " ".join(signature.split()).replace(" (", "(")


def method_name(method: Any, source: str) -> str:
    if method is None:
        return ""
    name = method.child_by_field_name("name")
    return node_text(name, source) if name is not None else ""


# --- classification ------------------------------------------------------------
def control_flow_of(node: Any) -> ControlFlow | None:
    """The control-flow construct a node represents, if it is one.

    ``if`` and ``else`` need their previous sibling to be disambiguated: an
    ``if_statement`` preceded by ``else`` is an else-if, and a bare block
    preceded by ``else`` is an else branch.
    """
    if node is None:
        return None
    previous = node.prev_sibling
    preceded_by_else = previous is not None and previous.type == "else"
    if node.type == "if_statement":
        return ControlFlow.ELSE_IF if preceded_by_else else ControlFlow.IF
    if node.type == "block" and preceded_by_else:
        return ControlFlow.ELSE
    return _DIRECT_FLOW.get(node.type)


def is_import_region(source: str, start_line: int, end_line: int) -> bool:
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
        if not stripped.startswith(("import", "package")):
            return False
        seen = True
    return seen
