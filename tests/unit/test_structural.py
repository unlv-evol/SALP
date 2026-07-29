"""Tree-sitter structural analysis.

Covers the behaviours adapted from unlv-evol/GACPD_Hunk_Context_Extraction plus
the corrections made on the way in: exact byte-slice text, no module-level parser
state, and methods selected by overlap rather than containment.
"""

from __future__ import annotations

import pytest

from salp.structural import (
    TREE_SITTER_AVAILABLE,
    class_structure,
    control_flow_constructs,
    describe,
    imported_class_names,
    imports_of,
    invoked_methods,
    locate,
    method_name,
    methods_overlapping,
    neighboring_methods,
    node_text,
    parse,
    query,
    referenced_classes,
)

pytestmark = pytest.mark.skipif(
    not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed"
)

SOURCE = """package org.example.demo;

import java.util.Objects;
import java.util.List;

public class Widget {
    private final int id;
    private final String name;

    public int getId() {
        return id;
    }

    public boolean matches(final String other, final List<String> pool) {
        if (other == null) {
            return false;
        }
        for (String candidate : pool) {
            if (Objects.equals(candidate, other)) {
                return true;
            }
        }
        return false;
    }

    public String getName() {
        return name;
    }

    static class Inner {
        private int depth;
        void tick() {}
    }
}
"""

# 1-based inclusive lines, as a unified-diff header reports them.
MATCHES_BODY = (16, 18)
CROSSES_METHODS = (12, 20)


# --- parsing and text ---------------------------------------------------------
def test_node_text_is_exact_and_not_off_by_one():
    """The original sliced with end_col + 1; end points are exclusive."""
    tree = parse(SOURCE)
    captures = query(tree.root_node, "(package_declaration) @p")
    assert node_text(captures["p"][0], SOURCE) == "package org.example.demo;"


def test_imports_are_recovered_in_order():
    tree = parse(SOURCE)
    assert imports_of(tree, SOURCE) == ["import java.util.Objects", "import java.util.List"]
    assert imported_class_names(tree, SOURCE) == ["Objects", "List"]


def test_parsing_is_not_shared_between_files():
    """Each tree is passed explicitly, so one file cannot see another's AST."""
    other = "package other;\nclass B { void z() {} }\n"
    assert imports_of(parse(other), other) == []
    assert imports_of(parse(SOURCE), SOURCE)  # unaffected by the parse above


# --- locating the edit region -------------------------------------------------
def test_locate_finds_the_enclosing_method_and_class():
    ctx = locate(SOURCE, *MATCHES_BODY)
    assert ctx.found
    assert ctx.class_name == "Widget"
    assert ctx.method_name == "matches"
    assert ctx.package == "org.example.demo"


def test_signature_uses_parameter_types_not_names():
    ctx = locate(SOURCE, *MATCHES_BODY)
    assert ctx.method_signature == "public boolean matches(String, List<String>)"


def test_a_region_crossing_methods_selects_by_overlap():
    """No single method contains such a region, so containment alone finds none."""
    ctx = locate(SOURCE, *CROSSES_METHODS)
    assert ctx.found
    assert len(ctx.overlapping_methods) > 1
    assert ctx.method_name is not None, "overlap must recover a method"
    assert "spans" in (ctx.diagnostics or "")


def test_methods_overlapping_matches_intersecting_ranges_only():
    tree = parse(SOURCE)
    names = [
        method_name(m, SOURCE) for m in methods_overlapping(tree, SOURCE, *CROSSES_METHODS)
    ]
    assert names == ["getId", "matches"]
    assert methods_overlapping(tree, SOURCE, 1, 2) == []  # package/import lines


def test_import_region_is_detected():
    ctx = locate(SOURCE, 3, 4)
    assert ctx.is_import_region
    assert ctx.method_name is None


def test_locate_on_unparseable_input_reports_rather_than_raises():
    assert locate("", 1, 2).found is False
    assert locate("!!! not java @@@", 1, 1).found is True  # tree-sitter is error-tolerant


# --- metadata -----------------------------------------------------------------
def test_class_structure_lists_direct_members_only():
    """Nested members must not be attributed to the outer class."""
    tree = parse(SOURCE)
    klass = query(tree.root_node, "(class_declaration) @c")["c"][0]
    structure = class_structure(klass, SOURCE)

    assert structure.name == "Widget"
    assert structure.fields == ["private final int id;", "private final String name;"]
    assert structure.methods == [
        "public int getId()",
        "public boolean matches(String, List<String>)",
        "public String getName()",
    ]
    (nested,) = structure.nested_classes
    assert nested.name == "Inner"
    assert nested.fields == ["private int depth;"]
    assert "private int depth;" not in structure.fields


def test_invocations_and_referenced_classes():
    ctx = locate(SOURCE, *MATCHES_BODY)
    method = ctx._method_node
    assert [i.text for i in invoked_methods(method, SOURCE)] == ["Objects.equals(candidate, other)"]
    # only identifiers that are actually imported count as references
    assert referenced_classes(method, SOURCE, ctx._tree) == ["List", "Objects"]


def test_neighbouring_methods():
    ctx = locate(SOURCE, *MATCHES_BODY)
    previous, following = neighboring_methods(ctx._method_node, SOURCE)
    assert previous.signature == "public int getId()"
    assert following.signature == "public String getName()"


def test_control_flow_constructs_are_classified():
    ctx = locate(SOURCE, *MATCHES_BODY)
    flow = control_flow_constructs(ctx._method_node)
    assert "if_statement" in flow
    assert "enhanced_for_statement" in flow
    assert "return_statement" in flow


def test_describe_assembles_the_whole_context():
    ctx = locate(SOURCE, *MATCHES_BODY)
    meta = describe(ctx, SOURCE)
    assert meta.package == "org.example.demo"
    assert meta.enclosing_class.name == "Widget"
    assert meta.referenced_classes == ["List", "Objects"]
    assert meta.previous_method.signature == "public int getId()"
    assert meta.as_dict()["package"] == "org.example.demo"


def test_describe_outside_a_method_still_reports_file_level_context():
    meta = describe(locate(SOURCE, 3, 4), SOURCE)
    assert meta.package == "org.example.demo"
    assert meta.enclosing_class is None
