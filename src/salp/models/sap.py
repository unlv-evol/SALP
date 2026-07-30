"""Package objects: functions, hunks, SAPs, and the pull-request grouping."""

from __future__ import annotations

from pydantic import BaseModel, Field

from salp.models.categories import CategoryEvidence, ChangeType
from salp.models.evidence import Provenance


class FunctionPayload(BaseModel):
    """De-duplicated, function-level payloads shared across the hunks of a file.

    Program text is stored as raw source files with the language extension, not
    as escaped JSON, so analysis tools can re-run on them and adaptation models
    can ingest them directly.
    """

    fn_id: str
    ext: str = "txt"
    has_source_before: bool = False  # f_s
    has_source_after: bool = False  # f'_s
    has_target: bool = False  # f_t
    has_ast: bool = False
    has_structure: bool = False

    @property
    def dir_ref(self) -> str:
        return f"functions/{self.fn_id}"

    @property
    def source_before_ref(self) -> str:
        return f"{self.dir_ref}/source.before.{self.ext}"

    @property
    def source_after_ref(self) -> str:
        return f"{self.dir_ref}/source.after.{self.ext}"

    @property
    def target_ref(self) -> str:
        return f"{self.dir_ref}/target.{self.ext}"

    @property
    def ast_ref(self) -> str:
        return f"{self.dir_ref}/ast.json"

    @property
    def structure_ref(self) -> str:
        return f"{self.dir_ref}/structure.json"


class TransformationUnit(BaseModel):
    """tau = (f_s, f'_s, f_t) plus the edit regions it applies to."""

    fn_id: str
    edit_regions: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    """A typed edge in the hunk index; evidence reduction traverses these."""

    src: str
    rel: str
    dst: str
    state: str = "PRESENT"
    evidence: str | None = None  # e.g. "same_pull_request" for weak PR-level edges


class Hunk(BaseModel):
    """One reusable edit region within a Transformation Unit."""

    hunk_id: str
    transformation: TransformationUnit
    categories: dict[str, CategoryEvidence] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)
    provenance: Provenance | None = None
    # Alignment findings that constrain Readiness (foundational conditions 5-6).
    localization_ambiguous: bool = False
    edit_region_unassociated: bool = False


class SAP(BaseModel):
    """A single reusable change (one MO file), possibly composite over hunks."""

    sap_id: str
    change_id: str
    change_type: ChangeType = ChangeType.MAPPED
    schema_version: str = "1.1"
    source_file: str | None = None
    target_file: str | None = None
    functions: dict[str, FunctionPayload] = Field(default_factory=dict)
    hunks: list[Hunk] = Field(default_factory=list)
    hunk_order: list[str] = Field(default_factory=list)  # composite ordering relation
    provenance: Provenance | None = None
    # SAP-relative path -> payload text. Keys are what index objects reference.
    # Excluded from serialization: index files carry no program text.
    payloads: dict[str, str] = Field(default_factory=dict, exclude=True)

    @property
    def is_composite(self) -> bool:
        return len(self.hunks) > 1

    def hunk(self, hunk_id: str) -> Hunk:
        for h in self.hunks:
            if h.hunk_id == hunk_id:
                return h
        raise KeyError(f"{self.sap_id} has no hunk {hunk_id!r}")

    def add_payload(self, ref: str, content: str) -> str:
        """Register a payload and return the reference index objects should use."""
        self.payloads[ref] = content
        return ref


class SAPReference(BaseModel):
    sap_id: str
    gacpd_classification: str
    path: str
    source_file: str | None = None
    target_file: str | None = None
    hunk_count: int = 0
    readiness_ref: str | None = None


class ContextFile(BaseModel):
    """An NA/ED file: referenced by the SAPs of its pull request, never minted."""

    gacpd_classification: str  # NA or ED
    source_file: str | None = None
    path: str | None = None  # None when GACPD retained no payload to copy
    role: str | None = None
    diagnostics: str | None = None


class PRGroup(BaseModel):
    """PR-level manifest: an index of SAPs, not a SAP itself.

    It has no Transformation Unit, no evidence categories, and no
    characterization of its own.
    """

    pr_id: str
    schema_version: str = "1.1"
    # The output grouping this pull request belongs to, target-first.
    variant_pair: str | None = None
    source_repo: str | None = None
    target_repo: str | None = None
    pull_request: dict[str, object] = Field(default_factory=dict)
    saps: list[SAPReference] = Field(default_factory=list)
    context_files: list[ContextFile] = Field(default_factory=list)
    cross_file_relationships: list[Relationship] = Field(default_factory=list)
    # PR-relative path -> payload text, for _context/ files. Excluded from
    # serialization: the manifest is an index, not a payload store.
    payloads: dict[str, str] = Field(default_factory=dict, exclude=True)
