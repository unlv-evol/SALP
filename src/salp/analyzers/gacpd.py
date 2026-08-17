"""Foundational analyzers, derived directly from GACPD output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from salp.analyzers.base import AnalysisContext, Analyzer, register
from salp.models import (
    Category,
    CategoryEvidence,
)


@dataclass


# --- Foundational (GACPD-derived) ------------------------------------------------
@register
class SourceChangeAnalyzer(Analyzer):
    category = Category.SOURCE_CHANGE
    component_name = "source-change"
    tool = "gacpd"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        d = self.draft(ctx, "not produced by GACPD")
        pin = ctx.source_pin
        d.present(
            "source_repo_revision",
            {"repo": ctx.source_repo, "revision": pin.commit if pin else None},
            pin=pin,
        )
        # The GACPD artifacts cover the changed region, not the whole function
        # body, so these carry the region diagnostic until structural analysis
        # can expand them to function boundaries.
        for element, present, filename in (
            ("pre_change_function", ctx.source_before, f"source.before.{ctx.ext}"),
            ("post_change_function", ctx.source_after, f"source.after.{ctx.ext}"),
        ):
            ref = f"functions/{ctx.fn_id}/{filename}"
            if present is None:
                d.unavailable(element, "GACPD emitted no region payload for this hunk")
            else:
                d.present(element, {"payload_ref": ref}, payload_ref=ref)
        if ctx.diff:
            d.present(
                "reusable_source_change", {"payload_ref": "hunk.diff"}, payload_ref="hunk.diff"
            )
        else:
            d.unavailable("reusable_source_change", "GACPD emitted no patch for this file")
        d.present(
            "affected_files_entities",
            {"source_file": ctx.source_file, "entities": [ctx.fn_id]},
        )
        d.present(
            "source_provenance",
            {"analysis_component": self.component_name, "input_artifacts": ctx.input_artifacts},
        )
        return d.build()


@register
class TargetLocalizationAnalyzer(Analyzer):
    category = Category.TARGET_LOCALIZATION
    component_name = "target-localization"
    tool = "gacpd"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        pin = ctx.target_pin
        raw_candidates: Any = ctx.extras.get("candidates") or []
        candidates = [str(c) for c in raw_candidates]
        confidence = ctx.extras.get("alignment_confidence")

        # §6: zero candidates is a failed localization, not a partial one. An MO
        # classification asserts a target region exists, so its absence means the
        # localization could not be represented at all.
        if not ctx.target_path and not candidates:
            return self.unavailable(
                ctx, "GACPD reported no target region for this file"
            )

        d = self.draft(ctx, "not produced by GACPD")

        d.present(
            "target_repo_revision",
            {"repo": ctx.target_repo, "revision": pin.commit if pin else None},
            pin=pin,
        )
        # "method" is a required field, so a payload that is only the enclosing
        # file scores 0.5 under the uniform rule. Recovering the file a target
        # function lives in is genuine partial evidence, but it is not f_t.
        fn = ctx.function
        target_ref = f"functions/{ctx.fn_id}/target.{ctx.ext}"
        d.present(
            "localized_target_function",
            {
                "payload_ref": target_ref if fn is None or fn.has_target else None,
                "method": fn.target_signature if fn and fn.has_target_function else None,
                "match_kind": fn.target_match_kind if fn else None,
                "diagnostics": (
                    fn.target_diagnostics or "payload is the enclosing file, not the function"
                    if fn and fn.target_is_whole_file else None
                ),
            },
            payload_ref=target_ref,
        )
        d.present("target_file_location", {"target_file": ctx.target_path})
        d.present(
            "source_target_correspondence",
            {"source_file": ctx.source_file, "target_file": ctx.target_path},
        )
        # An MO classification asserts a single successful mapping. Alternative
        # candidates are a completed investigation that found none -- verified
        # absence, not missing evidence.
        if candidates:
            d.present("alternative_candidates", {
                "candidates": candidates,
                "ambiguous": len(candidates) > 1,
            })
        else:
            d.absent("alternative_candidates", "GACPD localized a single target region (MO)")
        if confidence is None:
            d.unavailable("alignment_confidence", "GACPD reported no similarity for this hunk")
        else:
            # An MO classification asserts successful mapping, not high
            # similarity: a weak match is reduced confidence, not a failure.
            d.present(
                "alignment_confidence",
                {
                    "confidence": confidence,
                    "similarity": ctx.extras.get("similarity_breakdown"),
                },
            )
        d.present(
            "localization_provenance",
            {"analysis_component": self.component_name, "input_artifacts": ctx.input_artifacts},
        )
        return d.build()


@register
class TransformationAnalyzer(Analyzer):
    category = Category.FUNCTION_TRANSFORMATION
    component_name = "transformation"
    tool = "gacpd"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        d = self.draft(ctx, "not produced by GACPD")
        base = f"functions/{ctx.fn_id}"
        fn = ctx.function
        # tau is scored component-wise: recovering two of its three parts is a
        # partially represented unit, not a failed investigation. A member counts
        # only when the payload really is a function -- a diff region or a whole
        # file is a stand-in, and is reported in `approximated` instead.
        approximated = [
            name for name, stands_in in (
                ("f_s_before", fn.source_before_is_region if fn else False),
                ("f_s_after", fn.source_after_is_region if fn else False),
                ("f_t", fn.target_is_whole_file if fn else False),
            ) if stands_in
        ]
        recovered_nothing = fn is not None and not (
            fn.has_source_before_function
            or fn.has_source_after_function
            or fn.has_target_function
        )
        if recovered_nothing and fn is not None and fn.has_no_function_by_construction:
            # The file parsed and the edit region sits outside every method -- an
            # import block, a class parameter list. There is no function to
            # transform, so tau is not missing here, it does not apply, and it
            # leaves both denominators instead of scoring zero.
            where = (
                "in the import block" if fn.no_function_reason == "import_region"
                else "outside any method"
            )
            d.not_applicable(
                "transformation_unit",
                f"the edit region lies {where}, so it has no enclosing function; "
                "tau is undefined for this change rather than unrecovered",
            )
        elif recovered_nothing and fn is not None:
            # Every member is a stand-in, so tau was not recovered at all. The
            # diagnostic names the fix rather than leaving a bare zero.
            d.unavailable(
                "transformation_unit",
                "no member of tau could be resolved to a function body "
                f"({fn.no_function_reason}); GACPD supplies hunk regions and a whole "
                "target file. Clone the repositories with `salp fetch-repos` and "
                "install the `structural` extra.",
            )
        else:
            d.present(
                "transformation_unit",
                {
                    "f_s_before": (
                        f"{base}/source.before.{ctx.ext}"
                        if fn and fn.has_source_before_function else None
                    ),
                    "f_s_after": (
                        f"{base}/source.after.{ctx.ext}"
                        if fn and fn.has_source_after_function else None
                    ),
                    "f_t": f"{base}/target.{ctx.ext}" if fn and fn.has_target_function else None,
                    "signature": fn.signature if fn else None,
                    "approximated": approximated or None,
                    "derivation": ctx.extras.get("derivation_note"),
                },
            )
        d.present(
            "normalized_transformation",
            {"payload_ref": "transformation.json"},
            payload_ref="transformation.json",
        )
        d.present(
            "edit_regions",
            {
                "edit_regions": [f"{ctx.hunk_id}:ER-1"],
                "spans": ctx.extras.get("edit_region"),
                "diagnostics": ctx.extras.get("region_diagnostic"),
            },
        )
        d.present(
            "edit_entity_relationships",
            {"relationships": [{"from": f"{ctx.hunk_id}:ER-1", "rel": "aligned_to",
                                "to": f"functions/{ctx.fn_id}"}]},
        )
        # A single-hunk change is atomic: there is no ordering relation to recover.
        if ctx.is_composite:
            d.present("transformation_ordering", {"ordering": ctx.extras.get("hunk_order")})
        else:
            d.absent("transformation_ordering", "single-hunk change; no ordering relation applies")
        d.present(
            "transformation_provenance",
            {"analysis_component": self.component_name, "input_artifacts": ctx.input_artifacts},
        )
        return d.build()
