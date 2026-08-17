"""Recovery of tau = (f_s, f'_s, f_t) as three function bodies.

The specification asks for a transformation between two versions of the same
source function, applied to a corresponding target function. None of the three is
in the GACPD output: ``full_del``/``full_add`` are hunk regions in diff syntax and
``cmp/<File>`` is a whole file. Each has to be sliced out of the file at its
pinned repository state, which these tests cover in two parts -- undoing the
patch to reach the pre-change state, and matching the target function by
correspondence rather than by line number.
"""

from __future__ import annotations

import pytest

from salp.ingest import revert_patch
from salp.structural import (
    MATCH_EXACT,
    MATCH_NAME,
    MATCH_NAME_ARITY,
    TREE_SITTER_AVAILABLE,
    file_context,
    locate_method,
)

pytestmark = pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")


AFTER = """package org.example;

import java.time.Instant;

public class Widget {
    public int size(String name) {
        Instant start = Instant.now();
        return name.length();
    }

    private void reset() {
        this.count = 0;
    }
}
"""

PATCH = """@@ -6,4 +6,4 @@ public int size(String name) {
     public int size(String name) {
-        long start = System.nanoTime();
+        Instant start = Instant.now();
         return name.length();
"""


# --- undoing the patch --------------------------------------------------------
def test_the_pre_change_file_is_reconstructed_exactly():
    before = revert_patch(AFTER, PATCH)
    assert before is not None
    assert "long start = System.nanoTime();" in before
    assert "Instant start = Instant.now();" not in before
    # Everything outside the hunk is untouched.
    assert before.splitlines()[0] == "package org.example;"
    assert len(before.splitlines()) == len(AFTER.splitlines())


def test_reconstruction_refuses_a_patch_that_does_not_describe_the_file():
    """A wrong f_s is worse than an absent one: it is indistinguishable downstream."""
    drifted = AFTER.replace("return name.length();", "return name.trim().length();")
    assert revert_patch(drifted, PATCH) is None


def test_reconstruction_needs_both_inputs():
    assert revert_patch(None, PATCH) is None
    assert revert_patch(AFTER, None) is None
    assert revert_patch(AFTER, "not a diff") is None


def test_the_hunk_header_terminator_is_not_read_as_a_context_line():
    """The @@ match ends before its own newline; left in, it shifts the hunk by one."""
    before = revert_patch(AFTER, PATCH)
    assert before is not None and "package org.example;" in before


# --- matching the counterpart --------------------------------------------------
TARGET = """package org.other;

public class Widget {
    private void reset() {
        this.count = 0;
    }

    public int size(String name) {
        return name.length() + 1;
    }

    public int size(String name, int pad) {
        return name.length() + pad;
    }
}
"""


def test_an_exact_signature_match_is_reported_as_such():
    found = locate_method(TARGET, "public int size(String)", "size", "java")
    assert found.match_kind == MATCH_EXACT
    assert found.method_source is not None
    assert "return name.length() + 1;" in found.method_source


def test_the_counterpart_is_not_whatever_occupies_the_same_lines():
    """`reset` sits where `size` does in the source; position must not decide."""
    found = locate_method(TARGET, "public int size(String)", "size", "java")
    assert found.method_name == "size"


def test_a_differing_signature_falls_back_to_name_and_arity():
    found = locate_method(TARGET, "protected int size(CharSequence)", "size", "java")
    assert found.match_kind == MATCH_NAME_ARITY
    assert found.diagnostics is not None and "name and parameter count" in found.diagnostics


def test_a_differing_arity_falls_back_to_the_name_and_says_it_is_ambiguous():
    found = locate_method(TARGET, "public int size(String, int, int)", "size", "java")
    assert found.match_kind == MATCH_NAME
    assert found.diagnostics is not None
    assert "candidates" in found.diagnostics


def test_no_counterpart_is_reported_rather_than_invented():
    found = locate_method(TARGET, "public void absent()", "absent", "java")
    assert found.found and not found.has_method
    assert found.diagnostics is not None and "no method corresponding" in found.diagnostics


def test_a_method_body_is_recovered_whole_not_as_a_region():
    found = locate_method(TARGET, "public int size(String)", "size", "java")
    body = found.method_source or ""
    assert body.lstrip().startswith("public int size")
    assert body.rstrip().endswith("}")
    assert not body.lstrip().startswith("@@"), "a diff region is not a function"
    assert "package " not in body, "a whole file is not a function"


# --- regions with no enclosing function ---------------------------------------
def test_an_import_region_still_has_file_level_structure():
    ctx = file_context(TARGET, "java")
    assert ctx.found and not ctx.has_method
    assert ctx.package == "org.other"
