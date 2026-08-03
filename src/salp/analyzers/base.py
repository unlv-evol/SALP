"""The analyzer contract and the registry."""

from __future__ import annotations

import abc
from collections.abc import Iterable
from dataclasses import dataclass, field

from salp.models import (
    Category,
    CategoryEvidence,
    ChangeType,
    ElementSpec,
    EvidenceObject,
    EvidenceState,
    FunctionPayload,
    Provenance,
    RepositoryStatePin,
    elements_for,
)
from salp.structural import PARSEABLE_EXTENSIONS


@dataclass
class AnalysisContext:
    """Inputs available to every analyzer for one hunk.

    Carries the GACPD-derived artifacts for the hunk plus the identifiers the
    evidence objects must record. ``extras`` carries anything a particular
    analyzer needs (resolved commits, tool paths, prior results).
    """

    hunk_id: str
    fn_id: str
    source_file: str
    ext: str = "txt"
    source_before: str | None = None
    source_after: str | None = None
    diff: str | None = None
    target_path: str | None = None
    source_repo: str | None = None
    target_repo: str | None = None
    source_pin: RepositoryStatePin | None = None
    target_pin: RepositoryStatePin | None = None
    change_type: ChangeType = ChangeType.MAPPED
    hunk_index: int = 1
    hunk_count: int = 1
    input_artifacts: list[str] = field(default_factory=list)
    gacpd_dir: str | None = None
    # Whole files at the pinned repository states. GACPD supplies only hunk
    # regions, so analyses needing enclosing structure read these instead.
    source_file_text: str | None = None
    target_file_text: str | None = None
    # What the function pool actually recovered for this hunk's function: which
    # members of tau are real functions and which are stand-ins. Analyzers report
    # from this rather than from the presence of a GACPD artifact, so a payload
    # that is a diff region or a whole file is not claimed as a function.
    function: FunctionPayload | None = None
    extras: dict[str, object] = field(default_factory=dict)

    @property
    def is_composite(self) -> bool:
        """True when the SAP spans several hunks and needs an ordering relation."""
        return self.hunk_count > 1


class Analyzer(abc.ABC):
    """Base class for a single-category evidence investigation."""

    category: Category
    component_name: str = "unnamed-analyzer"
    tool: str | None = None
    version: str | None = None

    def tool_version(self) -> str | None:
        """The version of the analysis tool, recorded in provenance.

        Evidence is reproducible only under fixed tool versions and pinned
        repository states, so an analyzer backed by an external tool overrides
        this to report the version actually in use rather than a hardcoded one.
        """
        return self.version

    @abc.abstractmethod
    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        """Run the investigation and return category evidence."""

    # --- provenance ---------------------------------------------------------
    def _provenance(
        self,
        ctx: AnalysisContext | None = None,
        status: str = "ok",
        diagnostics: str | None = None,
        pin: RepositoryStatePin | None = None,
    ) -> Provenance:
        return Provenance(
            analysis_component=self.component_name,
            analysis_tool=self.tool,
            analysis_version=self.tool_version(),
            input_artifacts=list(ctx.input_artifacts) if ctx else [],
            analysis_status=status,
            diagnostics=diagnostics,
            repository_pin=pin,
        )

    # --- element construction ----------------------------------------------
    def _object_id(self, ctx: AnalysisContext, element_id: str) -> str:
        return f"{ctx.hunk_id}:{self.category.value}:{element_id}"

    def element(
        self,
        ctx: AnalysisContext,
        spec: ElementSpec,
        attributes: dict[str, object],
        *,
        payload_ref: str | None = None,
        blocking_conflict: bool = False,
        pin: RepositoryStatePin | None = None,
    ) -> EvidenceObject:
        """A recovered element, scored by the partial-representation rule.

        An element whose required fields are *all* missing was not recovered at
        all; it is recorded UNAVAILABLE rather than PRESENT with rep(e) = 0,
        which keeps Fidelity within (0, 1] as the specification requires.
        """
        rep = spec.representation(attributes)
        if rep == 0.0:
            return self.unavailable_element(
                ctx, spec, reason=f"no required field of {spec.element_id} was recovered"
            )
        missing = [f for f in spec.fields if f not in spec.represented_fields(attributes)]
        diagnostics = f"partially represented; missing: {', '.join(missing)}" if missing else None
        return EvidenceObject(
            object_id=self._object_id(ctx, spec.element_id),
            object_type=f"{self.category.value}.{spec.element_id}",
            state=EvidenceState.PRESENT,
            representation=rep,
            payload_ref=payload_ref,
            attributes=attributes,
            blocking_conflict=blocking_conflict,
            provenance=self._provenance(ctx, diagnostics=diagnostics, pin=pin),
        )

    def verified_absent_element(
        self, ctx: AnalysisContext, spec: ElementSpec, note: str | None = None
    ) -> EvidenceObject:
        """A completed investigation that established the phenomenon does not exist."""
        return EvidenceObject(
            object_id=self._object_id(ctx, spec.element_id),
            object_type=f"{self.category.value}.{spec.element_id}",
            state=EvidenceState.VERIFIED_ABSENT,
            provenance=self._provenance(ctx, status="verified_absent", diagnostics=note),
        )

    def unavailable_element(
        self, ctx: AnalysisContext, spec: ElementSpec, reason: str
    ) -> EvidenceObject:
        """An investigation that could not produce a reliable determination."""
        return EvidenceObject(
            object_id=self._object_id(ctx, spec.element_id),
            object_type=f"{self.category.value}.{spec.element_id}",
            state=EvidenceState.UNAVAILABLE,
            provenance=self._provenance(ctx, status="unavailable", diagnostics=reason),
        )

    # --- whole-category helpers --------------------------------------------
    def unavailable(self, ctx: AnalysisContext, reason: str) -> CategoryEvidence:
        """Every required element UNAVAILABLE, with a diagnostic on each."""
        return CategoryEvidence(
            category=self.category,
            elements=[
                self.unavailable_element(ctx, spec, reason)
                for spec in elements_for(self.category)
            ],
        )

    def verified_absent(self, ctx: AnalysisContext, note: str | None = None) -> CategoryEvidence:
        """Every required element VERIFIED_ABSENT: investigated, and confirmed empty."""
        return CategoryEvidence(
            category=self.category,
            elements=[
                self.verified_absent_element(ctx, spec, note)
                for spec in elements_for(self.category)
            ],
        )

    def not_applicable_element(
        self, ctx: AnalysisContext, spec: ElementSpec, reason: str
    ) -> EvidenceObject:
        """One element the change structurally cannot have."""
        return EvidenceObject(
            object_id=self._object_id(ctx, spec.element_id),
            object_type=f"{self.category.value}.{spec.element_id}",
            state=EvidenceState.NOT_APPLICABLE,
            provenance=self._provenance(ctx, status="not_applicable", diagnostics=reason),
        )

    def not_applicable(self, ctx: AnalysisContext, reason: str) -> CategoryEvidence:
        """Every required element NOT_APPLICABLE: the category does not apply here.

        Distinct from VERIFIED_ABSENT, which asserts a phenomenon was looked for
        and found not to exist. This asserts the question is not asked at all for
        this change type, so the category leaves both the Coverage and the
        Fidelity denominator. The reason is recorded on every element.
        """
        return CategoryEvidence(
            category=self.category,
            elements=[
                self.not_applicable_element(ctx, spec, reason)
                for spec in elements_for(self.category)
            ],
        )

    def draft(self, ctx: AnalysisContext, default_reason: str) -> CategoryDraft:
        """Start a category in which every element is UNAVAILABLE, then override."""
        return CategoryDraft(self, ctx, default_reason)


class CategoryDraft:
    """Accumulates one outcome per required element of a category.

    Every element starts UNAVAILABLE with ``default_reason``; an analyzer marks
    the ones it resolved. Because the draft is seeded from the specification's
    element catalog, an element an analyzer forgets stays an explicit
    UNAVAILABLE with a diagnostic instead of vanishing from the denominator.
    """

    def __init__(self, analyzer: Analyzer, ctx: AnalysisContext, default_reason: str) -> None:
        self._analyzer = analyzer
        self._ctx = ctx
        self._specs = {s.element_id: s for s in elements_for(analyzer.category)}
        self._objects: dict[str, EvidenceObject] = {
            eid: analyzer.unavailable_element(ctx, spec, default_reason)
            for eid, spec in self._specs.items()
        }

    def _spec(self, element_id: str) -> ElementSpec:
        try:
            return self._specs[element_id]
        except KeyError:  # pragma: no cover - programming error, not data
            raise KeyError(
                f"{self._analyzer.category.value} has no required element {element_id!r}"
            ) from None

    def present(
        self,
        element_id: str,
        attributes: dict[str, object],
        *,
        payload_ref: str | None = None,
        blocking_conflict: bool = False,
        pin: RepositoryStatePin | None = None,
    ) -> CategoryDraft:
        self._objects[element_id] = self._analyzer.element(
            self._ctx,
            self._spec(element_id),
            attributes,
            payload_ref=payload_ref,
            blocking_conflict=blocking_conflict,
            pin=pin,
        )
        return self

    def absent(self, element_id: str, note: str | None = None) -> CategoryDraft:
        self._objects[element_id] = self._analyzer.verified_absent_element(
            self._ctx, self._spec(element_id), note
        )
        return self

    def unavailable(self, element_id: str, reason: str) -> CategoryDraft:
        self._objects[element_id] = self._analyzer.unavailable_element(
            self._ctx, self._spec(element_id), reason
        )
        return self

    def not_applicable(self, element_id: str, reason: str) -> CategoryDraft:
        """One element the change structurally cannot have, in an otherwise live category."""
        self._objects[element_id] = self._analyzer.not_applicable_element(
            self._ctx, self._spec(element_id), reason
        )
        return self

    def build(self) -> CategoryEvidence:
        return CategoryEvidence(
            category=self._analyzer.category,
            elements=[self._objects[eid] for eid in self._specs],
        )


_REGISTRY: dict[Category, type[Analyzer]] = {}


def register(cls: type[Analyzer]) -> type[Analyzer]:
    """Class decorator: register an analyzer for its category."""
    if not getattr(cls, "category", None):
        raise ValueError(f"{cls.__name__} must set a `category`")
    _REGISTRY[cls.category] = cls
    return cls


def get(category: Category) -> type[Analyzer] | None:
    return _REGISTRY.get(category)


def build_all() -> list[Analyzer]:
    """Instantiate one analyzer per registered category."""
    return [cls() for cls in _REGISTRY.values()]


def registered_categories() -> Iterable[Category]:
    return _REGISTRY.keys()


# Extensions some grammar claims. Anything else is an explicit UNAVAILABLE rather
# than a silent skip. Claimed but uninstalled is a separate, more precise message
# from `salp.structural.diagnostic_for`, so this is only the coarse gate.
_PARSEABLE = PARSEABLE_EXTENSIONS
