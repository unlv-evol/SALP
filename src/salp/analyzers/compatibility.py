"""API and dependency compatibility between the pinned states."""

from __future__ import annotations

from dataclasses import dataclass

from salp.analyzers.base import (
    _PARSEABLE,
    AnalysisContext,
    Analyzer,
    CategoryDraft,
    register,
)
from salp.models import (
    Category,
    CategoryEvidence,
)
from salp.structural import (
    imports_of,
    package_of,
    parse,
)


@dataclass


@register
class CompatibilityAnalyzer(Analyzer):
    """Compares the APIs and dependencies the change needs against the target.

    The APIs a change references are its imports; the dependencies backing them
    are declared in the build files. Both sides are read at their pinned states,
    so the comparison is between the exact revisions the SAP is bound to.
    """

    category = Category.COMPATIBILITY
    component_name = "compatibility"
    tool = "import-and-build-analysis"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        d = self.draft(ctx, "no file available at the pinned repository state")
        if ctx.ext not in _PARSEABLE:
            return self.unavailable(ctx, f"no grammar configured for .{ctx.ext}")
        if ctx.source_file_text is None:
            return self.unavailable(ctx, "source file not available; run `salp fetch-repos`")

        source_imports = imports_of(parse(ctx.source_file_text), ctx.source_file_text)
        d.present("source_apis", {"apis": source_imports})

        if ctx.target_file_text is None:
            d.unavailable("target_apis", "target file not available; run `salp fetch-repos`")
            target_imports: list[str] = []
        else:
            target_imports = imports_of(parse(ctx.target_file_text), ctx.target_file_text)
            d.present("target_apis", {"apis": target_imports})

        # An import the change introduces that the target does not already have
        # is what adaptation must reconcile; one it already has needs no mapping.
        introduced = [i for i in source_imports if i not in set(target_imports)]
        if ctx.target_file_text is None:
            d.unavailable("api_mappings", "no target file to compare against")
        elif introduced:
            d.present("api_mappings", {"mappings": [
                {"api": i, "present_in_target": False, "resolution": "introduce"}
                for i in introduced
            ]})
        else:
            d.absent("api_mappings", "every API the change references already exists in the target")

        source_deps = ctx.extras.get("source_dependencies")
        target_deps = ctx.extras.get("target_dependencies")
        for element, deps, side in (
            ("source_dependencies", source_deps, "source"),
            ("target_dependencies", target_deps, "target"),
        ):
            if deps is None:
                d.unavailable(element, f"no {side} build file found at the pinned state")
            elif deps:
                d.present(element, {"dependencies": deps})
            else:
                d.absent(element, f"the {side} build file declares no dependencies")

        d = self._record_findings(
            d, introduced, target_deps, package_of(ctx.source_file_text)
        )
        d.present(
            "compatibility_provenance",
            {"analysis_component": self.component_name, "analysis_tool": self.tool},
        )
        return d.build()

    @staticmethod
    def _record_findings(
        d: CategoryDraft,
        introduced: list[str],
        target_deps: object,
        own_package: str | None,
    ) -> CategoryDraft:
        """Record unresolved compatibility constraints on third-party APIs.

        Deliberately does *not* raise a Blocking Conflict. The specification
        reserves that for a concrete obstacle that prevents safe integration --
        an irreconcilable incompatibility -- and it caps Readiness at Low
        regardless of Coverage and Fidelity. Reading declarations out of a build
        file cannot establish irreconcilability: coordinates arrive through
        version catalogs, parent projects, and transitive resolution that a
        textual scan does not see. The finding is reported; the judgement is not.
        """
        if not isinstance(target_deps, list):
            d.unavailable("compatibility_findings", "target dependencies unknown")
            return d

        known = " ".join(str(dep).lower() for dep in target_deps)
        findings = []
        for api in introduced:
            package = api.removeprefix("import ").removeprefix("static ").strip()
            # java.*/javax.* are platform APIs and need no declared dependency;
            # the project's own packages are not dependencies at all.
            if package.startswith(("java.", "javax.")) or _same_project(package, own_package):
                continue
            root = ".".join(package.split(".")[:2])
            findings.append(
                {"api": package, "dependency_declared": bool(root and root.lower() in known)}
            )

        if not findings:
            d.absent("compatibility_findings", "the change introduces no third-party API")
        else:
            d.present("compatibility_findings", {"findings": findings})
        return d


def _same_project(package: str, own_package: str | None) -> bool:
    """Whether an import belongs to the same project as the file importing it.

    Compared on the shared leading segments of the two package names: two or more
    in common means the same project. Erring toward "same project" is deliberate
    -- omitting a finding is far less harmful than fabricating one against a
    dependency the target does in fact declare.
    """
    if not own_package:
        return False
    a, b = package.split("."), own_package.split(".")
    shared = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        shared += 1
    return shared >= 2
