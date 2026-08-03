"""Per-language node vocabulary for tree-sitter parsing.

Every structural analysis in SALP is written once against this vocabulary rather
than against one grammar's node names. Adding a language means adding a
``Grammar`` here, not another branch in the analyzers.

Availability is per language, not global. tree-sitter itself, the Java binding,
and the Scala binding are three separate installs, and a missing one is reported
by name -- ``grammar_for`` returns None and the caller records UNAVAILABLE with a
diagnostic saying which package would fix it. Nothing here raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache, lru_cache
from importlib import import_module
from typing import Any

# tree-sitter ships as the optional "structural" extra. An analyzer whose parser
# is unavailable records UNAVAILABLE with a diagnostic rather than failing.
try:  # pragma: no cover - import guard
    from tree_sitter import Language, Parser, Query, QueryCursor

    TREE_SITTER_AVAILABLE = True
    IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    TREE_SITTER_AVAILABLE = False
    IMPORT_ERROR = f"{exc}; install the 'structural' extra"


class ControlFlow(StrEnum):
    """Control-flow constructs recognised around an edit region.

    A language-neutral vocabulary. The values read like Java node names because
    that is the grammar SALP started from and they are part of the written SAP
    schema; each grammar maps its own node types onto them.
    """

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


@dataclass(frozen=True)
class Grammar:
    """The node types one language uses for the constructs SALP reasons about."""

    name: str
    module: str  # the tree-sitter binding, imported only when first needed
    extensions: frozenset[str]
    root: str
    class_like: frozenset[str]
    method_like: frozenset[str]
    # The node holding a formal parameter list, and one parameter within it. A
    # language may have several parameter lists per method (Scala currying), so
    # both are sets and every match contributes.
    parameters: frozenset[str]
    parameter: frozenset[str]
    package: str
    # Child types of the package node that carry the name, most specific first.
    package_name: tuple[str, ...]
    import_declaration: str
    # Line prefixes that make a line part of the import preamble.
    import_prefixes: tuple[str, ...]
    if_node: str
    else_token: str
    block: str
    # Members of a class-like declaration, for surrounding metadata.
    class_body: str
    field_like: frozenset[str]
    invocation: frozenset[str]
    # Node types that can name an imported type inside a method body.
    reference_like: frozenset[str]
    control_flow: dict[str, ControlFlow] = field(default_factory=dict)

    @property
    def boundary(self) -> frozenset[str]:
        """Node types an upward walk stops at."""
        return self.class_like | self.method_like | {self.root}

    def available(self) -> bool:
        return TREE_SITTER_AVAILABLE and _binding(self.module) is not None

    def missing(self) -> str:
        """Why this grammar cannot be used, naming the package that would fix it."""
        if not TREE_SITTER_AVAILABLE:
            return IMPORT_ERROR or "tree-sitter unavailable"
        return f"no {self.name} grammar installed; pip install {self.module.replace('_', '-')}"


JAVA = Grammar(
    name="Java",
    module="tree_sitter_java",
    extensions=frozenset({"java"}),
    root="program",
    class_like=frozenset({
        "class_declaration", "interface_declaration",
        "enum_declaration", "record_declaration",
    }),
    method_like=frozenset({"method_declaration", "constructor_declaration"}),
    parameters=frozenset({"formal_parameters"}),
    parameter=frozenset({"formal_parameter", "spread_parameter"}),
    package="package_declaration",
    package_name=("scoped_identifier", "identifier"),
    import_declaration="import_declaration",
    import_prefixes=("import", "package"),
    if_node="if_statement",
    else_token="else",
    block="block",
    class_body="class_body",
    field_like=frozenset({"field_declaration"}),
    invocation=frozenset({"method_invocation"}),
    reference_like=frozenset({"scoped_identifier", "type_identifier", "identifier"}),
    control_flow={
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
    },
)


# Scala models almost everything as an expression rather than a statement, so the
# node names differ throughout even where the construct is the same. Two things
# have no Java counterpart and are mapped onto the nearest term: a for
# comprehension binds names like an enhanced for rather than counting like a C
# for, and `match` is the switch. `object`, `trait`, and `given` are class-like;
# `function_declaration` is an abstract `def` with no body, which is still a
# method for the purpose of locating an edit region.
SCALA = Grammar(
    name="Scala",
    module="tree_sitter_scala",
    extensions=frozenset({"scala", "sc"}),
    root="compilation_unit",
    class_like=frozenset({
        "class_definition", "object_definition", "trait_definition",
        "enum_definition", "given_definition",
    }),
    method_like=frozenset({"function_definition", "function_declaration"}),
    parameters=frozenset({"parameters", "class_parameters"}),
    parameter=frozenset({"parameter", "class_parameter"}),
    package="package_clause",
    package_name=("package_identifier", "identifier"),
    import_declaration="import_declaration",
    import_prefixes=("import", "package"),
    if_node="if_expression",
    else_token="else",
    block="block",
    class_body="template_body",
    field_like=frozenset({"val_definition", "var_definition"}),
    invocation=frozenset({"call_expression"}),
    reference_like=frozenset({"stable_identifier", "type_identifier", "identifier"}),
    control_flow={
        "for_expression": ControlFlow.ENHANCED_FOR,
        "while_expression": ControlFlow.WHILE,
        "do_while_expression": ControlFlow.DO,
        "match_expression": ControlFlow.SWITCH,
        "case_clause": ControlFlow.SWITCH_CASE,
        "try_expression": ControlFlow.TRY,
        "throw_expression": ControlFlow.THROW,
        "return_expression": ControlFlow.RETURN,
    },
)


GRAMMARS: tuple[Grammar, ...] = (JAVA, SCALA)

# Every extension some grammar claims, whether or not its binding is installed.
# A claimed-but-uninstalled extension is a precise diagnostic; an unclaimed one
# is "no grammar configured".
PARSEABLE_EXTENSIONS = frozenset(e for g in GRAMMARS for e in g.extensions)

_BY_EXTENSION = {e: g for g in GRAMMARS for e in g.extensions}


@cache
def _binding(module: str) -> Any | None:
    """The tree-sitter language binding, or None when it is not installed."""
    try:  # pragma: no cover - import guard
        return import_module(module)
    except ImportError:
        return None


def grammar_for(ext: str | None) -> Grammar | None:
    """The grammar for a file extension, or None when there is none to use.

    None covers both "no grammar claims this extension" and "the binding is not
    installed"; ``diagnostic_for`` distinguishes them for the caller's message.
    """
    grammar = _BY_EXTENSION.get((ext or "").lstrip(".").lower())
    return grammar if grammar is not None and grammar.available() else None


def diagnostic_for(ext: str | None) -> str:
    """Why no grammar is available for an extension."""
    grammar = _BY_EXTENSION.get((ext or "").lstrip(".").lower())
    if grammar is None:
        return f"no tree-sitter grammar configured for .{ext}"
    return grammar.missing()


# Keyed by module name rather than by Grammar: a Grammar carries a dict of
# control-flow mappings, which makes it unhashable, and the module name is what
# actually identifies the loaded binding.
@lru_cache(maxsize=len(GRAMMARS))
def _language(module: str) -> Any | None:
    """The loaded Language, or None when the binding is not installed.

    Callers reach this only after ``available()``, but returning None rather
    than raising keeps the "nothing here raises" contract intact if they do not.
    """
    binding = _binding(module)
    return Language(binding.language()) if binding is not None else None


@lru_cache(maxsize=len(GRAMMARS))
def _parser(module: str) -> Any | None:
    language = _language(module)
    return Parser(language) if language is not None else None


def parse(source: str, grammar: Grammar | None) -> Any | None:
    """Parse source with a grammar, or None when either is unavailable."""
    if grammar is None or not source:
        return None
    parser = _parser(grammar.module)
    return parser.parse(source.encode("utf-8")) if parser is not None else None


def query(node: Any, pattern: str, grammar: Grammar | None) -> dict[str, list[Any]]:
    """Run a tree-sitter query over a subtree, returning its captures."""
    if node is None or grammar is None:
        return {}
    language = _language(grammar.module)
    if language is None:
        return {}
    return QueryCursor(Query(language, pattern)).captures(node)
