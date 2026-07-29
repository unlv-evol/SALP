"""Evidence primitives: states, provenance, and repository-state pins."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class EvidenceState(StrEnum):
    """Outcome of an evidence investigation.

    These states are mutually exclusive and must never be omitted: an
    investigation that produced no result is recorded explicitly, not dropped.
    """

    PRESENT = "PRESENT"  # investigation completed and recovered valid evidence
    VERIFIED_ABSENT = "VERIFIED_ABSENT"  # completed; confirmed no applicable evidence
    UNAVAILABLE = "UNAVAILABLE"  # could not determine (failure/unsupported/missing input)


class RepositoryStatePin(BaseModel):
    """Binds an evidence object to an exact repository state for reproducibility.

    ``resolved_from`` records how the commit was reached -- a pull-request ref or
    a cutoff date -- and ``diagnostics`` says why an unresolved pin stayed
    date-based, so the binding is auditable either way.
    """

    repo: str
    commit: str | None = None
    content_hash: str | None = None
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    resolved_from: str | None = None
    diagnostics: str | None = None

    @property
    def is_resolved(self) -> bool:
        """A pin is fully resolved only when a commit (or content hash) is known.

        GACPD emits dates rather than SHAs, so pins built from GACPD output alone
        are date-based and unresolved until SALP resolves them against a local
        clone.
        """
        return bool(self.commit or self.content_hash)


class Provenance(BaseModel):
    """How an evidence object was produced."""

    analysis_component: str
    analysis_tool: str | None = None
    analysis_version: str | None = None
    input_artifacts: list[str] = Field(default_factory=list)
    analysis_status: str = "ok"
    diagnostics: str | None = None
    repository_pin: RepositoryStatePin | None = None


class EvidenceObject(BaseModel):
    """A single adaptation-relevant entity within the SAP.

    ``representation`` is the Fidelity representation score for a PRESENT element:
    1.0 when fully represented, a value in (0, 1) when partial. It is ignored for
    non-PRESENT states (Fidelity is computed over PRESENT elements only).
    """

    object_id: str
    object_type: str
    state: EvidenceState
    representation: float = 1.0
    payload_ref: str | None = None
    attributes: dict[str, object] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None
    # A concrete obstacle to safe integration, distinct from VERIFIED_ABSENT.
    # When true on any object, Readiness is capped at Low.
    blocking_conflict: bool = False

    def model_post_init(self, __context: object) -> None:
        if not 0.0 <= self.representation <= 1.0:
            raise ValueError("representation must lie in [0, 1]")
