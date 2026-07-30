"""Foundational analyzers, derived directly from GACPD output."""

from __future__ import annotations

from dataclasses import dataclass

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
        d = self.draft(ctx, "not produced by GACPD")
        pin = ctx.target_pin
        candidates = ctx.extras.get("candidates") or []
        confidence = ctx.extras.get("alignment_confidence")

        d.present(
            "target_repo_revision",
            {"repo": ctx.target_repo, "revision": pin.commit if pin else None},
            pin=pin,
        )
        d.present(
            "localized_target_function",
            {"payload_ref": f"functions/{ctx.fn_id}/target.{ctx.ext}"},
            payload_ref=f"functions/{ctx.fn_id}/target.{ctx.ext}",
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
            d.present("alternative_candidates", {"candidates": candidates})
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
        # tau is scored component-wise: recovering two of its three parts is a
        # partially represented unit, not a failed investigation.
        d.present(
            "transformation_unit",
            {
                "f_s_before": f"{base}/source.before.{ctx.ext}" if ctx.source_before else None,
                "f_s_after": f"{base}/source.after.{ctx.ext}" if ctx.source_after else None,
                "f_t": f"{base}/target.{ctx.ext}" if ctx.target_path else None,
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
