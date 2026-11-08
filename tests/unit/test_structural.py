"""Tree-sitter structural analysis.

Covers the behaviours adapted from unlv-evol/GACPD_Hunk_Context_Extraction plus
the corrections made on the way in: exact byte-slice text, no module-level parser
state, and methods selected by overlap rather than containment.
"""

from __future__ import annotations

import pytest

from salp.structural import (
    JAVA,
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
    tree = parse(SOURCE, JAVA)
    captures = query(tree.root_node, "(package_declaration) @p", JAVA)
    assert node_text(captures["p"][0], SOURCE) == "package org.example.demo;"


def test_imports_are_recovered_in_order():
    tree = parse(SOURCE, JAVA)
    assert imports_of(tree, SOURCE, JAVA) == [
        "import java.util.Objects", "import java.util.List",
    ]
    assert imported_class_names(tree, SOURCE, JAVA) == ["Objects", "List"]


def test_parsing_is_not_shared_between_files():
    """Each tree is passed explicitly, so one file cannot see another's AST."""
    other = "package other;\nclass B { void z() {} }\n"
    assert imports_of(parse(other, JAVA), other, JAVA) == []
    assert imports_of(parse(SOURCE, JAVA), SOURCE, JAVA)  # unaffected by the parse above


# --- locating the edit region -------------------------------------------------
def test_locate_finds_the_enclosing_method_and_class():
    ctx = locate(SOURCE, *MATCHES_BODY, "java")
    assert ctx.found
    assert ctx.class_name == "Widget"
    assert ctx.method_name == "matches"
    assert ctx.package == "org.example.demo"


def test_signature_uses_parameter_types_not_names():
    ctx = locate(SOURCE, *MATCHES_BODY, "java")
    assert ctx.method_signature == "public boolean matches(String, List<String>)"


def test_a_region_crossing_methods_selects_by_overlap():
    """No single method contains such a region, so containment alone finds none."""
    ctx = locate(SOURCE, *CROSSES_METHODS, "java")
    assert ctx.found
    assert len(ctx.overlapping_methods) > 1
    assert ctx.method_name is not None, "overlap must recover a method"
    assert "spans" in (ctx.diagnostics or "")


def test_methods_overlapping_matches_intersecting_ranges_only():
    tree = parse(SOURCE, JAVA)
    names = [
        method_name(m, SOURCE)
        for m in methods_overlapping(tree, SOURCE, *CROSSES_METHODS, JAVA)
    ]
    assert names == ["getId", "matches"]
    assert methods_overlapping(tree, SOURCE, 1, 2, JAVA) == []  # package/import lines


def test_import_region_is_detected():
    ctx = locate(SOURCE, 3, 4, "java")
    assert ctx.is_import_region
    assert ctx.method_name is None


def test_locate_on_unparseable_input_reports_rather_than_raises():
    assert locate("", 1, 2, "java").found is False
    assert locate("!!! not java @@@", 1, 1, "java").found is True  # tree-sitter is error-tolerant


# --- metadata -----------------------------------------------------------------
def test_class_structure_lists_direct_members_only():
    """Nested members must not be attributed to the outer class."""
    tree = parse(SOURCE, JAVA)
    klass = query(tree.root_node, "(class_declaration) @c", JAVA)["c"][0]
    structure = class_structure(klass, SOURCE, JAVA)

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
    ctx = locate(SOURCE, *MATCHES_BODY, "java")
    method = ctx._method_node
    calls = [i.text for i in invoked_methods(method, SOURCE, JAVA)]
    assert calls == ["Objects.equals(candidate, other)"]
    # only identifiers that are actually imported count as references
    assert referenced_classes(method, SOURCE, ctx._tree, JAVA) == ["List", "Objects"]


def test_neighbouring_methods():
    ctx = locate(SOURCE, *MATCHES_BODY, "java")
    previous, following = neighboring_methods(ctx._method_node, SOURCE, JAVA)
    assert previous.signature == "public int getId()"
    assert following.signature == "public String getName()"


def test_control_flow_constructs_are_classified():
    ctx = locate(SOURCE, *MATCHES_BODY, "java")
    flow = control_flow_constructs(ctx._method_node, JAVA)
    assert "if_statement" in flow
    assert "enhanced_for_statement" in flow
    assert "return_statement" in flow


def test_describe_assembles_the_whole_context():
    ctx = locate(SOURCE, *MATCHES_BODY, "java")
    meta = describe(ctx, SOURCE)
    assert meta.package == "org.example.demo"
    assert meta.enclosing_class.name == "Widget"
    assert meta.referenced_classes == ["List", "Objects"]
    assert meta.previous_method.signature == "public int getId()"
    assert meta.as_dict()["package"] == "org.example.demo"


def test_describe_outside_a_method_still_reports_file_level_context():
    meta = describe(locate(SOURCE, 3, 4, "java"), SOURCE)
    assert meta.package == "org.example.demo"
    assert meta.enclosing_class is None


# --- target correspondence ------------------------------------------------------
_RENAMED = """package org.example.demo;

public class Widget {
    public int count() {
        return 2;
    }
}
"""

_DIVERGED = """package org.example.demo;

public class Widget {
    public int size() {
        return 1;
    }
}
"""


def _structural(function):
    from salp.analyzers.base import AnalysisContext
    from salp.analyzers.structural import StructuralAnalyzer

    ce = StructuralAnalyzer().investigate(AnalysisContext(
        hunk_id="H-1", fn_id="Widget_count", source_file="Widget.java", ext="java",
        source_file_text=_RENAMED, target_file_text=_DIVERGED,
        edit_span=(4, 4), edit_text=_RENAMED,
        function=function,
    ))
    return next(e for e in ce.elements if e.object_type.endswith("target_structure"))


def test_a_renamed_method_is_matched_to_the_target_from_its_pre_change_name():
    """The change renames size() to count(); the target, not yet changed, has size().

    The post-change name cannot exist in the target, so the counterpart is found
    only from the pre-change method the function pool matched f_t from.
    """
    from salp.models import FunctionPayload

    pooled = FunctionPayload(
        fn_id="Widget_count", ext="java",
        match_signature="public int size()", match_method_name="size",
    )
    target = _structural(pooled)
    assert target.state.value == "PRESENT"
    assert target.attributes["method"] == "public int size()"


def test_without_a_pooled_match_the_post_change_method_has_no_counterpart():
    target = _structural(None)
    assert target.state.value == "UNAVAILABLE"
    assert "no method corresponding" in (target.provenance.diagnostics or "")


# --- verbatim payload slices --------------------------------------------------
_GROUPED_IMPORTS = """package a.b;

import java.io.File;
import java.util.List;

// fork-only
import kafka.X;

public class C {
    void m() {}
}"""


def test_the_import_block_is_cut_verbatim_with_its_blank_lines_and_comments():
    """A consumer locates the block by its text, so every byte between groups counts."""
    from salp.structural import import_block

    block = import_block(parse(_GROUPED_IMPORTS, JAVA), _GROUPED_IMPORTS, JAVA)
    assert block == (
        "import java.io.File;\nimport java.util.List;\n\n// fork-only\nimport kafka.X;\n"
    )
    assert _GROUPED_IMPORTS.count(block) == 1


def test_imports_inside_a_scala_object_are_not_part_of_the_file_import_block():
    """A slice reaching a nested import would take the object body with it."""
    from salp.structural import SCALA, import_block

    source = "package p\n\nimport a.B\nimport c.D\n\nobject X {\n  import e.F\n}\n"
    assert import_block(parse(source, SCALA), source, SCALA) == "import a.B\nimport c.D\n"


def test_the_import_run_touching_the_edit_is_the_one_cut():
    from salp.structural import SCALA, import_block

    source = "package p\nimport a.B\n\npackage q\nimport c.D\nimport e.F\n"
    tree = parse(source, SCALA)
    assert import_block(tree, source, SCALA) == "import a.B\n"
    assert import_block(tree, source, SCALA, (4, 4)) == "import c.D\nimport e.F\n"


def test_a_class_payload_is_whole_lines_through_its_final_newline():
    """A region splice refuses a region whose last line has no newline."""
    from salp.structural import find_class_in_target

    target = "package a;\n\npublic class C {\n    void m() {}\n}\n\nclass D {}\n"
    found = find_class_in_target("C", target, JAVA)
    assert found is not None
    assert found.class_source == "public class C {\n    void m() {}\n}\n"
    assert target.count(found.class_source) == 1


def test_a_located_class_payload_is_whole_lines_too():
    ctx = locate(_GROUPED_IMPORTS + "\n", 9, 9, "java")
    assert ctx.class_source == "public class C {\n    void m() {}\n}\n"


def test_a_slice_at_the_end_of_a_file_without_a_final_newline_stays_verbatim():
    """Nothing is invented: the file's last line has no terminator to include."""
    from salp.structural import find_class_in_target

    found = find_class_in_target("C", _GROUPED_IMPORTS, JAVA)
    assert found is not None
    assert found.class_source == "public class C {\n    void m() {}\n}"
