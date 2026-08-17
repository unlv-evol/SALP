"""Structural and surrounding analyzers, backed by tree-sitter."""

from __future__ import annotations

import json
from dataclasses import asdict

from salp.analyzers.base import (
    AnalysisContext,
    Analyzer,
    register,
)
from salp.analyzers.tools import tree_sitter_version
from salp.models import (
    Category,
    CategoryEvidence,
)
from salp.structural import (
    EditContext,
    describe,
    diagnostic_for,
    file_context,
    grammar_for,
    locate,
    locate_method,
    to_ast_dict,
)


def _method_ast(ctx: EditContext, source: str) -> dict[str, object]:
    """The normalized AST of the located method, or of nothing when outside one."""
    if ctx._method_node is None:
        return {"note": "edit region lies outside any method", "package": ctx.package}
    return to_ast_dict(ctx._method_node, source)


def _correspondences(
    source_ctx: EditContext, target_ctx: EditContext | None
) -> list[dict[str, object]]:
    """Source-to-target structural correspondences, and whether each aligns."""
    if target_ctx is None or not target_ctx.found:
        return []
    pairs = [
        ("package", source_ctx.package, target_ctx.package),
        ("class", source_ctx.class_name, target_ctx.class_name),
        ("method", source_ctx.method_signature, target_ctx.method_signature),
    ]
    return [
        {"kind": kind, "source": s, "target": t, "aligned": bool(s and t and s == t)}
        for kind, s, t in pairs
        if s or t
    ]


# --- SALP-enriched --------------------------------------------------------------
@register
class StructuralAnalyzer(Analyzer):
    """Recovers program structure around the edit region on both sides.

    Reads the whole source and target files at their pinned states -- GACPD
    supplies only hunk regions, which carry no enclosing class, package, or
    method boundary -- and locates the edit region within each.
    """

    category = Category.STRUCTURAL
    component_name = "structural-ast"
    tool = "tree-sitter"

    def tool_version(self) -> str | None:
        return tree_sitter_version()

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        if grammar_for(ctx.ext) is None:
            return self.unavailable(ctx, diagnostic_for(ctx.ext))

        spans = ctx.extras.get("edit_region") or {}
        before = spans.get("source_before") if isinstance(spans, dict) else None
        source_ctx = self._locate(ctx.source_file_text, before, ctx.ext)
        # The target is found by *correspondence*, never by the source's line
        # span: a diverged variant has drifted in position, so those line numbers
        # land on whatever happens to occupy them. On the bitcoinj sample that
        # resolved `saveNow` to `saveNowInternal` and reported it as PRESENT.
        target_ctx = self._corresponding(ctx.target_file_text, source_ctx, ctx.ext)

        d = self.draft(ctx, "no file available at the pinned repository state")
        ast_ref = f"functions/{ctx.fn_id}/ast.json"
        ast: dict[str, object] = {}

        if source_ctx is not None and source_ctx.found:
            ast["source"] = _method_ast(source_ctx, ctx.source_file_text or "")
            d.present(
                "source_structure",
                {"payload_ref": ast_ref, "class": source_ctx.class_name,
                 "method": source_ctx.method_signature, "package": source_ctx.package},
                payload_ref=ast_ref,
            )
        elif ctx.source_file_text is None:
            d.unavailable("source_structure", "source file not available; run `salp fetch-repos`")

        # A source method with no counterpart in the target is a failed
        # correspondence, not a recovered one. Only a region that never had an
        # enclosing method -- an import-block edit -- is legitimately reported
        # from file-level structure alone.
        expected_method = source_ctx is not None and source_ctx.has_method
        no_counterpart = (
            target_ctx is not None and target_ctx.found
            and expected_method and not target_ctx.has_method
        )

        if target_ctx is not None and target_ctx.found and not no_counterpart:
            ast["target"] = _method_ast(target_ctx, ctx.target_file_text or "")
            # match_kind and diagnostics are informational: they record how
            # strong the correspondence is without altering rep(e), which the
            # specification derives from the required fields alone.
            d.present(
                "target_structure",
                {"payload_ref": ast_ref, "class": target_ctx.class_name,
                 "method": target_ctx.method_signature, "package": target_ctx.package,
                 "match_kind": target_ctx.match_kind,
                 "diagnostics": target_ctx.diagnostics},
                payload_ref=ast_ref,
            )
        elif ctx.target_file_text is None:
            d.unavailable("target_structure", "target file not available; run `salp fetch-repos`")
        elif target_ctx is not None:
            d.unavailable(
                "target_structure",
                target_ctx.diagnostics or "no corresponding method found in the target file",
            )

        if source_ctx is not None and source_ctx.found:
            d.present(
                "edit_region_structure",
                {"spans": spans,
                 "enclosing_method_span": source_ctx.method_span,
                 "immediate_construct": source_ctx.immediate_construct,
                 "is_import_region": source_ctx.is_import_region},
            )
            d.present(
                "structural_correspondences",
                {"correspondences": _correspondences(source_ctx, target_ctx)},
            )
            d.present(
                "structure_transformation_link",
                {"relationships": [
                    {"from": f"{ctx.hunk_id}:ER-1", "rel": "declared_in",
                     "to": source_ctx.method_signature or source_ctx.class_name},
                ]},
            )
        d.present(
            "structural_provenance",
            {"analysis_component": self.component_name, "analysis_tool": self.tool},
        )

        evidence = d.build()
        if ast:
            evidence.payloads[ast_ref] = json.dumps(ast, indent=2)
        return evidence

    @staticmethod
    def _locate(text: str | None, span: object, ext: str) -> EditContext | None:
        if not text or not isinstance(span, dict):
            return None
        return locate(text, int(span["start"]), int(span["end"]), ext)

    @staticmethod
    def _corresponding(
        text: str | None, source_ctx: EditContext | None, ext: str
    ) -> EditContext | None:
        """The target-side counterpart of the source's enclosing method.

        A region with no enclosing method -- an import-block edit -- has no method
        to correspond to, but the file around it still has recoverable structure.
        """
        if not text or source_ctx is None or not source_ctx.found:
            return None
        if not source_ctx.has_method:
            return file_context(text, ext)
        return locate_method(text, source_ctx.method_signature or "", source_ctx.method_name, ext)


@register
class SurroundingAnalyzer(Analyzer):
    category = Category.SURROUNDING
    component_name = "surrounding"
    tool = "tree-sitter"

    def tool_version(self) -> str | None:
        return tree_sitter_version()

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        """Recover the program context beyond the edited method itself.

        The NA/ED siblings come from GACPD; the enclosing type, module context,
        callers, callees, and related entities come from the parsed source file
        at its pinned state.
        """
        siblings = ctx.extras.get("context_files") or []
        d = self.draft(ctx, "pending surrounding-context recovery")

        if siblings:
            d.present("repository_context", {"context_files": siblings})
        else:
            d.absent("repository_context", "pull request touched no NA/ED sibling files")

        spans = ctx.extras.get("edit_region") or {}
        before = spans.get("source_before") if isinstance(spans, dict) else None
        # Only parse what an installed grammar actually covers: running one
        # language's grammar over another yields an error tree, not evidence.
        if grammar_for(ctx.ext) is None or not (ctx.source_file_text and before):
            return d.build()

        located = locate(
            ctx.source_file_text, int(before["start"]), int(before["end"]), ctx.ext
        )
        if not located.found:
            return d.build()
        meta = describe(located, ctx.source_file_text)

        if meta.enclosing_class is not None:
            d.present(
                "enclosing_context",
                {"enclosing": asdict(meta.enclosing_class)},
            )
        d.present("file_module_context", {"modules": [meta.package] if meta.package else []})

        callees = [i.text for i in meta.invoked_methods]
        neighbours = [
            m.signature for m in (meta.previous_method, meta.next_method) if m is not None
        ]
        if callees or neighbours:
            # GACPD gives no call graph, so callers stay unrecovered: what the
            # file alone can show is what this method calls and what sits beside it.
            d.present("callers_callees", {"callees": callees, "callers": neighbours})
        else:
            d.absent("callers_callees", "the edited method invokes nothing and has no neighbours")

        if meta.referenced_classes:
            d.present("related_entities", {"entities": meta.referenced_classes})
        else:
            d.absent("related_entities", "the edited method references no imported class")

        d.present(
            "surrounding_provenance",
            {"analysis_component": self.component_name, "input_artifacts": ctx.input_artifacts},
        )
        return d.build()
