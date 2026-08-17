# External analysis tools

Third-party binaries and grammars the SALP-enriched analyzers shell out to.
Contents are git-ignored — these are large, platform-specific, and separately
versioned. Only this README and the `.gitkeep` files are tracked.

Point `configs/default.yaml` at whatever you install here:

```yaml
tools:
  refactoringminer_jar: ./tools/refactoringminer/RefactoringMiner.jar
  tree_sitter_lib: ./tools/tree-sitter
```

Both default to `null`, meaning "not configured". An analyzer whose tool is
unconfigured must record `UNAVAILABLE` with a diagnostic rather than failing the
build — an unrun investigation is an information gap, not an error.

## `refactoringminer/`

Detects the structural evolution between the pinned source and target states:
renames, extractions, moves, inlining, signature changes. Feeds
`refactorings.json` (`Category.REFACTORING`, weight 2, semantic support).

Unpack the official release here. It is a *distribution* — a launcher script
plus a `lib/` directory — not a single fat jar, so point the config at the
launcher:

```
tools/refactoringminer/
  RefactoringMiner-3.1.4/
    bin/RefactoringMiner     <- tools.refactoringminer_jar points here
    lib/*.jar
```

The version is read from the distribution directory name and recorded in
evidence provenance as `analysis_version`, so results stay reproducible as the
tool evolves. A `VERSION` file beside the launcher overrides it, for builds laid
out differently.

Requires Java on `PATH` (17+ for 3.1.4).

**It is the slowest analysis in the pipeline, by a wide margin.** Cost tracks
repository size as much as commit count, so a modest range over a large
repository can run for many minutes. Three controls:

| Control | Effect |
| --- | --- |
| `tools.refactoringminer_timeout` | seconds before one repository is abandoned (default 900) |
| `detect_refactorings: false`, or `salp run --no-refactorings` | skip it; the category becomes UNAVAILABLE with a diagnostic |
| `data/repos/.refactoring-cache/` | results are cached per (repo, start, end), so only the first run over a range pays |

A timed-out or skipped analysis is an information gap, never a failure: the run
continues and the category records why.

When a run completes and finds no relevant refactoring, the analyzer must emit
`VERIFIED_ABSENT`, **not** `UNAVAILABLE`. That distinction is load-bearing:
verified absence takes full Coverage credit and is excluded from Fidelity, which
is what stops a simple change from being penalised for a phenomenon that simply
does not occur. Treating a missing result as verified absence is called out in
the specification as a common representation mistake.

## `tree-sitter/`

Parses `f_s`, `f'_s`, and `f_t` into normalized ASTs and structural context.
Feeds `functions/<fn_id>/ast.json` and `structure.json` (`Category.STRUCTURAL`).

The Python bindings and the Java grammar are declared as the optional
`structural` extra, so the usual route needs nothing in this directory:

```bash
pip install -e ".[structural]"
```

Use this folder only for grammars you build yourself, or languages without a
packaged binding:

```
tools/tree-sitter/
  <language>.so       # compiled grammar
  VERSION
```

Store normalized structural representations, never raw parser output — evidence
objects must stay independent of any particular parser implementation.

## Adding another tool

Analyzers are discovered through a registry, so wiring a tool in touches no
orchestrator code. Give it a directory here, add a path to the `Tools` model in
`src/salp/config.py`, put the runner in `src/salp/analyzers/tools.py`, and add a
module under `src/salp/analyzers/` registering an analyzer for its category:

```python
from salp.analyzers import AnalysisContext, Analyzer, register
from salp.models import Category, CategoryEvidence


@register
class MyRefactoringAnalyzer(Analyzer):
    category = Category.REFACTORING
    component_name = "my-refactoring"
    tool = "RefactoringMiner"
    version = "3.0.9"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        d = self.draft(ctx, "tool not configured")
        ...
        return d.build()
```

Import the new module from `analyzers/__init__.py` — that import *is* the
registration — and a registered class replaces the built-in for its category.
Build the category through `self.draft(...)` so every required information element carries
an explicit outcome; an element the analyzer never reaches stays an explicit
`UNAVAILABLE` with a diagnostic instead of dropping out of the Coverage
denominator. Record `analysis_tool` and `analysis_version` on every object.
