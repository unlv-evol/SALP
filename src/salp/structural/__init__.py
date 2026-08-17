"""Tree-sitter structural analysis, per language.

Adapted from the ``Making_AST`` package of
unlv-evol/GACPD_Hunk_Context_Extraction; see
``docs/adoption/gacpd-hunk-context-extraction.md``.

Java and Scala are supported. Every analysis is written once against the node
vocabulary in ``grammars.py`` rather than against one grammar's node names, so a
language is added by declaring a ``Grammar``, not by branching in the analyzers.

tree-sitter and each language binding ship in the optional ``structural`` extra,
and availability is checked per language: ``grammar_for`` returns None when a
binding is missing, and ``diagnostic_for`` says which package would fix it.
Analyzers record UNAVAILABLE with that diagnostic rather than failing, and never
fall back to another language's grammar.
"""

from salp.structural.context import (
    MATCH_EXACT,
    MATCH_NAME,
    MATCH_NAME_ARITY,
    EditContext,
    file_context,
    imported_class_names,
    imports_of,
    locate,
    locate_method,
    methods_overlapping,
    package_of,
    to_ast_dict,
)
from salp.structural.grammars import (
    GRAMMARS,
    IMPORT_ERROR,
    JAVA,
    PARSEABLE_EXTENSIONS,
    SCALA,
    TREE_SITTER_AVAILABLE,
    ControlFlow,
    Grammar,
    diagnostic_for,
    grammar_for,
    parse,
    query,
)
from salp.structural.metadata import (
    ClassStructure,
    Invocation,
    MethodRef,
    SurroundingMetadata,
    class_structure,
    control_flow_constructs,
    describe,
    invoked_methods,
    neighboring_methods,
    referenced_classes,
)
from salp.structural.syntax import (
    control_flow_of,
    enclosing_class,
    enclosing_method,
    is_import_region,
    is_in_method,
    method_name,
    method_signature,
    node_text,
    parameter_type,
    sort_by_position,
)

__all__ = [
    "GRAMMARS",
    "IMPORT_ERROR",
    "JAVA",
    "MATCH_EXACT",
    "MATCH_NAME",
    "MATCH_NAME_ARITY",
    "PARSEABLE_EXTENSIONS",
    "SCALA",
    "TREE_SITTER_AVAILABLE",
    "ClassStructure",
    "ControlFlow",
    "EditContext",
    "Grammar",
    "Invocation",
    "MethodRef",
    "SurroundingMetadata",
    "class_structure",
    "control_flow_constructs",
    "control_flow_of",
    "describe",
    "diagnostic_for",
    "enclosing_class",
    "enclosing_method",
    "file_context",
    "grammar_for",
    "imported_class_names",
    "imports_of",
    "invoked_methods",
    "is_import_region",
    "is_in_method",
    "locate",
    "locate_method",
    "method_name",
    "method_signature",
    "methods_overlapping",
    "neighboring_methods",
    "node_text",
    "package_of",
    "parameter_type",
    "parse",
    "query",
    "referenced_classes",
    "sort_by_position",
    "to_ast_dict",
]
