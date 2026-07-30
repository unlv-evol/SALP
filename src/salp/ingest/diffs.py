"""Unified-diff parsing for GACPD hunk artifacts.

GACPD emits, per hunk, a ``hunk_<n>_full_del`` and ``hunk_<n>_full_add`` file.
Each begins with the unified-diff header for that hunk and is followed by the
pre-change or post-change lines of the region:

    @@ -513,6 +516,10 @@ public void close() throws Exception {
             List<Entry<String, Future<?>>> futureEntries = new ArrayList<>();
             ...

Two things are recoverable from that header without parsing any source:

*Edit-region spans* -- the ``-start,count`` and ``+start,count`` ranges give the
region's location in the pre- and post-change file.

*Enclosing function* -- the trailing section heading is the function the hunk
sits in. Hunks sharing a heading occupy the same function and must therefore
share one entry in the function pool rather than duplicating it.

Note what these artifacts are *not*: they cover the changed region plus its diff
context, not the whole function body. Expanding a region to true function
boundaries requires the structural analysis, so payloads built from these
artifacts are recorded as partially represented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# @@ -old_start,old_count +new_start,new_count @@ optional section heading
_HUNK_HEADER = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@[ \t]?(.*)$", re.MULTILINE
)

# A heading counts as a declaration only when it opens with declaration
# modifiers, which a truncated continuation line will not.
_MODIFIERS = (
    r"public|private|protected|static|final|abstract|synchronized|native|"
    r"transient|volatile|strictfp|default|def|fun|func|override|suspend|inline|"
    r"implicit|operator|class|interface|enum|record|object|trait"
)
_DECLARATION = re.compile(rf"^\s*(?:(?:{_MODIFIERS})\s+)+[\w<>\[\],.\s]*?(\w+)\s*[(<]")


@dataclass(frozen=True)
class HunkHeader:
    """The parsed ``@@`` line of one unified-diff hunk."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str | None = None

    @property
    def old_end(self) -> int:
        return self.old_start + max(self.old_count, 1) - 1

    @property
    def new_end(self) -> int:
        return self.new_start + max(self.new_count, 1) - 1

    def spans(self) -> dict[str, object]:
        """The edit-region spans this header describes."""
        return {
            "source_before": {"start": self.old_start, "end": self.old_end,
                              "line_count": self.old_count},
            "source_after": {"start": self.new_start, "end": self.new_end,
                             "line_count": self.new_count},
            "section": self.section,
        }

    def group_key(self) -> str | None:
        """The grouping key for the enclosing function: the normalized heading.

        Two hunks belong to the same function exactly when git reported the same
        section heading for them. Grouping on the whole heading is safe even
        when no name can be extracted from it.
        """
        return " ".join(self.section.split()) if self.section else None

    def declared_name(self) -> str | None:
        """The declared function name, when the heading really is a declaration.

        Git's section heading is a best-effort "last line that looked like a
        declaration", so it is sometimes a truncated continuation line such as
        ``metaProperties, config, new MetadataRecordSerde(), metadataPartition``.
        Naming a function pool entry from that would assert something false, so
        a name is taken only when the heading opens with declaration modifiers.
        Returns None otherwise, and the caller falls back to a positional id.
        """
        if not self.section:
            return None
        return m.group(1) if (m := _DECLARATION.search(self.section)) else None


def parse_hunk_header(text: str | None) -> HunkHeader | None:
    """Parse the leading ``@@`` header of a GACPD hunk artifact."""
    if not text:
        return None
    m = _HUNK_HEADER.search(text)
    if m is None:
        return None
    old_start, old_count, new_start, new_count, section = m.groups()
    return HunkHeader(
        old_start=int(old_start),
        old_count=int(old_count) if old_count is not None else 1,
        new_start=int(new_start),
        new_count=int(new_count) if new_count is not None else 1,
        section=section.strip() or None,
    )


def hunk_side(diff: str | None, *, side: str) -> str | None:
    """Reconstruct one side of a hunk from its unified diff.

    GACPD omits ``hunk_<n>_full_add`` for a pure-deletion hunk and
    ``hunk_<n>_full_del`` for a pure insertion, because there is nothing to
    write. The content is still recoverable exactly: the pre-change side is the
    context and deleted lines, the post-change side the context and added ones.
    The ``@@`` header is retained so the result matches the shape of the
    artifacts GACPD does emit.

    ``side`` is ``"before"`` or ``"after"``.
    """
    if not diff:
        return None
    keep = "-" if side == "before" else "+"
    drop = "+" if side == "before" else "-"

    lines: list[str] = []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            lines.append(line)
        elif (
            not in_hunk
            or line.startswith(("---", "+++", "diff ", "index "))
            or line.startswith(drop)
        ):
            continue
        elif line.startswith(keep):
            lines.append(" " + line[1:])
        else:  # context, or the "\ No newline at end of file" marker
            if not line.startswith("\\"):
                lines.append(line)
    return "\n".join(lines) + "\n" if lines else None


def split_patch(patch: str | None) -> list[tuple[HunkHeader, str]]:
    """Split a unified diff into one ``(header, text)`` pair per hunk.

    GACPD stores a single whole-file ``.patch``; the SAP stores one ``hunk.diff``
    per hunk. Each slice keeps the file header (``---``/``+++``) so it remains a
    self-contained, appliable diff.
    """
    if not patch:
        return []
    matches = list(_HUNK_HEADER.finditer(patch))
    if not matches:
        return []

    preamble = patch[: matches[0].start()]
    file_header = "".join(
        line + "\n"
        for line in preamble.splitlines()
        if line.startswith(("---", "+++", "diff ", "index "))
    )

    slices: list[tuple[HunkHeader, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(patch)
        header = parse_hunk_header(patch[m.start() : m.end()])
        if header is None:  # pragma: no cover - the match guarantees a header
            continue
        slices.append((header, file_header + patch[m.start() : end]))
    return slices
