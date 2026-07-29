"""Tree-sitter structural analysis of Java source.

Adapted from the ``Making_AST`` package of
unlv-evol/GACPD_Hunk_Context_Extraction; see
``docs/adoption/gacpd-hunk-context-extraction.md``.

tree-sitter ships as the optional ``structural`` extra. When absent,
``TREE_SITTER_AVAILABLE`` is False and analyzers record UNAVAILABLE with a
diagnostic instead of failing.
"""

from salp.structural.context import (
    EditContext,
    imported_class_names,
    imports_of,
    locate,
    methods_overlapping,
    package_of,
    to_ast_dict,
)
from salp.structural.java import (
    CLASS_LIKE,
    IMPORT_ERROR,
    METHOD_LIKE,
    TREE_SITTER_AVAILABLE,
    ControlFlow,
    control_flow_of,
    enclosing_class,
    enclosing_method,
    is_import_region,
    is_in_method,
    method_name,
    method_signature,
    node_text,
    parameter_type,
    parse,
    query,
    sort_by_position,
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

__all__ = [
    "CLASS_LIKE",
    "ClassStructure",
    "ControlFlow",
    "EditContext",
    "IMPORT_ERROR",
    "Invocation",
    "METHOD_LIKE",
    "MethodRef",
    "SurroundingMetadata",
    "TREE_SITTER_AVAILABLE",
    "class_structure",
    "control_flow_constructs",
    "control_flow_of",
    "describe",
    "enclosing_class",
    "enclosing_method",
    "imported_class_names",
    "imports_of",
    "invoked_methods",
    "is_import_region",
    "is_in_method",
    "locate",
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
