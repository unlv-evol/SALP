# SALP — Semantic Alignment Pipeline

SALP recovers deterministic semantic evidence from **GACPD** output and constructs
**characterized Semantic Alignment Packages (SAPs)** — the standardized artifact
consumed by the Reusable Change Adaptation Pipeline (RCAP).

Scope of this starter: **MO-classified files only.** Each MO file becomes one
file-scoped SAP (composite over its hunks); NA/ED files are retained as context and
never minted as SAPs.

## Architecture

SALP has two entry points. `fetch-repos` is the only step that touches the
network; `run` is local, deterministic, and reads what `fetch-repos` cached.

```
salp fetch-repos ─▶ data/repos/                bare clones + pull-request refs

salp run
  data/gacpd/ ─▶ ingest             discover MO files; parse records and hunk diffs
                 repos              resolve state pins; read files at those commits
                 analyzers          one investigation per evidence category
                   ├ gacpd            source change · localization · transformation
                   ├ structural  ───▶ structural/       (tree-sitter Java, Scala)
                   ├ compatibility    imports + build-file declarations
                   ├ verification     target-side tests, via git grep
                   └ refactoring ───▶ analyzers/tools/  (RefactoringMiner)
                 packaging          assemble the SAP, then validate it
                 characterization   Coverage · Fidelity · Readiness
                 packaging          write the canonical on-disk layout
                     │
                     ▼
                 data/out/<variant-pair>/PR-<n>/sap-<file>/
```

A SAP is therefore *GACPD output plus SALP enrichment*: GACPD supplies the
foundational evidence, and everything else is recovered from the repositories at
their pinned commits. What an analyzer cannot recover is recorded as an explicit
`UNAVAILABLE` with a diagnostic, never omitted — so the characterization reflects
how much enrichment has actually run.

## Quickstart

```bash
make dev        # editable install with the dev *and* structural extras
make check      # ruff, mypy, pytest — what CI runs
```

Install the `structural` extra, not just `dev` — `make dev` does this for you,
and the equivalent is `pip install -e ".[dev,structural]"`. It carries
tree-sitter and the Java and Scala grammars, without which the structural,
surrounding, and compatibility analyzers all report `UNAVAILABLE`. A run still
succeeds, but on the reference sample mean Coverage over all 25 hunks falls from
0.940 to 0.676 and **every one of the eleven packages caps at Low** — without a
grammar the function transformation cannot be sliced out of the pinned files, and
an `UNAVAILABLE` foundational element caps Readiness at Low regardless of
Coverage.

Grammars are checked one at a time. Having tree-sitter and `tree-sitter-java` but
not `tree-sitter-scala` degrades only the Scala files, and says so by name.

Then point it at a GACPD run:

```bash
# 1. drop a real GACPD run under data/gacpd/  (see data/gacpd/README.md)
salp -c configs/default.yaml fetch-repos --dry-run   # what would be cloned
salp -c configs/default.yaml fetch-repos             # clone them (network)
salp -c configs/default.yaml run                     # construct SAPs (local)
```

`make fetch` and `make run` are shorthands for the last two. On the reference
sample this mints **11 SAPs / 25 hunks** across 6 pull requests and 2 variant
pairs, in 417 files:

```
data/out/
  linkedinKafka-apacheKafka/
    PR-12535/  pr.json  _context/  sap-CombinedKey/
  langerhansDogecoinjNew-bitcoinjBitcoinj/
    PR-2731/   pr.json  _context/  sap-WalletFiles/  sap-MnemonicCode/  ...
```

`salp validate` re-checks written SAPs against the schema — every required file
present, every index reference resolving, every object identifier unique, every
evidence object carrying a valid state and provenance, and the composite ordering
total. It exits non-zero on any finding, so CI gates on it.

Per-command flags: `--gacpd-run`, `--output`, and `--repo-cache` override the
config; `--no-resolve-pins` skips commit resolution; `fetch-repos --dry-run`
reports without cloning. `--log-level` is global, so it goes *before* the
subcommand — `salp --log-level DEBUG run`. `salp categories` lists which analyzer
is registered for each evidence category.

Three directories are git-ignored and populated locally:

| Directory | Holds | See |
| --- | --- | --- |
| `data/gacpd/` | Real GACPD run output — the default `paths.gacpd_run` | [README](data/gacpd/README.md) |
| `data/repos/` | Bare clones of the source and target repositories | [README](data/repos/README.md) |
| `tools/` | RefactoringMiner and other external binaries | [README](tools/README.md) |

## Repository layout

```
configs/default.yaml     runtime configuration
data/gacpd/              GACPD run output          (git-ignored, see its README)
data/repos/              bare clones               (git-ignored, see its README)
tools/                   external tool binaries    (git-ignored, see its README)
docs/adoption/           what was reused from prior work, and how it changed
src/salp/                the package
tests/unit/              no network, no sample, no clones
tests/integration/       runs against data/gacpd/; skipped when absent
data/out/                generated SAPs            (git-ignored)
```

## Package layout

Layered by pipeline stage; each package has one public surface via its `__init__`:

```
src/salp/
  cli.py                  # `salp run`, `salp fetch-repos`, `salp categories`
  config.py               # YAML config + logging setup
  pipeline.py             # orchestrator
  py.typed                # ships type information (PEP 561)

  models/                 # the data model
    evidence.py           #   states, provenance, repository-state pins
    categories.py         #   categories, weights, change-type profiles
    elements.py           #   required information elements per category
    sap.py                #   functions, hunks, SAPs, PR grouping

  ingest/                 # reading GACPD output
    gacpd.py              #   discovery walk (MO scope)
    records.py            #   pr_results.txt / results.txt parsers
    diffs.py              #   unified-diff headers, slicing, side reconstruction

  repos/                  # git-backed repository access
    git.py                #   command-line wrapper
    cache.py              #   bare clones
    pins.py               #   state-pin resolution
    files.py              #   pinned-state files, grep, build declarations

  structural/             # tree-sitter, per language
    grammars.py           #   node vocabulary per language; Java and Scala
    syntax.py             #   navigation and signatures, grammar-driven
    context.py            #   locating an edit region's context
    metadata.py           #   class/method/control-flow metadata

  analyzers/              # one investigation per evidence category
    base.py               #   contract, draft, registry
    gacpd.py              #   source change, localization, transformation
    structural.py         #   structural + surrounding
    compatibility.py      #   APIs and dependencies
    verification.py       #   target-side oracle discovery
    refactoring.py        #   RefactoringMiner
    standalone.py         #   artifact identity + target placement
    tools.py              #   external tool runners

  characterization/       # scoring
    levels.py             #   qualitative levels
    engine.py             #   Coverage / Fidelity / Readiness

  packaging/              # producing the SAP
    builder.py            #   assembly
    validation.py         #   mandatory validity conditions
    writer.py             #   canonical on-disk layout
```

### Layout principles

Each subpackage owns one stage of the pipeline and exposes a single public
surface through its `__init__.py`, so callers depend on `salp.repos` rather than
`salp.repos.cache`. Internals can be reorganised without touching call sites.

Dependencies run one way: `models` depends on nothing; `ingest`, `repos`, and
`structural` depend on `models`; `analyzers` depends on those; `characterization`
and `packaging` sit above; `pipeline` wires them together.


## Index and payload

A SAP separates an **index** — small, program-text-free objects carrying evidence
states and typed relationships — from **payloads**, the bulky artifacts those
objects reference. Payloads travel on `SAP.payloads`, keyed by the same
SAP-relative path an index object stores in `payload_ref`, and are written out as
raw source files with their language extension. The output tree is:

```
PR-<n>/
  pr.json                      # manifest: SAP list, PR provenance, cross-file edges
  _context/<file>              # NA/ED siblings, referenced but never minted
  sap-<file>/
    sap.json  characterization.json  provenance.json  change.json
    functions/<fn_id>/         # de-duplicated across the file's hunks
      source.before.<ext>  source.after.<ext>  target.<ext>
      ast.json  structure.json
    hunks/<hunk_id>/
      hunk.json                # INDEX SPINE: states, refs, confidence, edges
      hunk.diff  edit_region.json  transformation.json  localization.json
      refactorings.json  compatibility.json  surrounding.json
      verification.json  provenance.json
```

## The function transformation

The adaptation pipeline is built around a transformation between two versions of
one source function, applied to a corresponding target function:

```
τ = (f_s, f'_s, f_t)
```

`f_s` and `f'_s` are the source function before and after the change; `f_t` is
the target function before adaptation. All three are **function bodies**, and
none of them is in the GACPD output: `hunk_<n>_full_del` / `full_add` are hunk
regions in diff syntax, complete with `@@` headers and `-`/`+` prefixes, and
`cmp/<File>` is a whole file. Each is therefore sliced out of the file at its
pinned repository state, in `packaging/builder.py`:

| Member | How it is recovered |
| --- | --- |
| `f'_s` | the method enclosing the edit region in the pinned source file, which sits at the pull-request head and is thus the *post*-change state |
| `f_s` | the same method in that file with the patch undone (`ingest.revert_patch`) |
| `f_t` | the *corresponding* method in the pinned target file (`structural.locate_method`) |

`f_t` is matched by **signature**, never by the source's line span. A diverged
variant has drifted in position as well as content, so the source's line numbers
land on whatever happens to occupy them — on the reference sample that resolved
`saveNow` to `saveNowInternal` and reported it as recovered. Matching degrades in
three recorded steps — exact signature, then name and arity, then name alone —
and an overload resolved by name alone is flagged ambiguous rather than picked
silently.

Two outcomes are deliberately distinguished when no function is found:

* an edit region in the **import block** has no enclosing function by
  construction, so τ is `NOT_APPLICABLE` and leaves both denominators;
* a function that exists but could not be sliced — no clone, no grammar — is
  `UNAVAILABLE` with a diagnostic naming the fix, and caps Readiness at Low.

`revert_patch` verifies every context and added line against the file it claims
to describe and returns nothing on any mismatch. A wrong `f_s` is worse than an
absent one: downstream it is indistinguishable from a real one.

## Required information elements

Each category declares the information elements the specification requires the
pipeline to produce (`models/elements.py`). Characterization runs over those
elements, which is what makes both metrics graded rather than all-or-nothing.
Every category defines one uniform partial-representation rule: an element
declares the attribute keys needed for full representation, and

```
rep(e) = |represented fields| / |required fields|
```

An element whose fields are *all* missing was not recovered at all and is
recorded `UNAVAILABLE`, keeping Fidelity within `(0, 1]`. Analyzers build a
category through a draft that seeds every element as `UNAVAILABLE`, so an
element an analyzer forgets stays an explicit gap with a diagnostic instead of
vanishing from the Coverage denominator.

## Repository state binding

A SAP is bound to the exact state of every artifact it was built from, so it is
reproducible and can be invalidated when a bound target state changes. GACPD
emits dates and a pull-request number rather than commit SHAs, so SALP resolves
them against local bare clones — over the git wire protocol, with **no GitHub API
calls**, no token, and no rate limit:

| What | How |
| --- | --- |
| Source commit | `git fetch origin refs/pull/<n>/head` — a git ref, not a REST call |
| Target commit | `git rev-list -1 --before=<cutoff> HEAD` |
| File at a pin | `git show <sha>:<path>`, no working tree checked out |

`fetch-repos` is the only step that touches the network; `run` reads the cache
locally. A missing clone is an information gap, not an error: the pin stays
date-based with a diagnostic naming the fix, and the affected element is
partially represented rather than silently complete.

`make check` runs exactly what CI runs (ruff, mypy, pytest); `make help` lists
the rest. See [CONTRIBUTING.md](CONTRIBUTING.md) to add an analysis.

## The characterization model (as implemented)

* **Evidence states** — four, each behaving differently under characterization:

  | State | Coverage | Fidelity |
  | --- | --- | --- |
  | `PRESENT` | resolved | scored |
  | `VERIFIED_ABSENT` | resolved | excluded |
  | `UNAVAILABLE` | 0 | excluded |
  | `NOT_APPLICABLE` | **excluded** | **excluded** |

  `NOT_APPLICABLE` leaves *both* denominators — that is what lets a standalone
  artifact change, which has no Transformation Unit, still reach High Readiness
  rather than being capped by categories it structurally cannot have.
* **Coverage** — investigation completion over the categories *required for the
  change type*. Optional and `NOT_APPLICABLE` categories never reduce it.
* **Fidelity** — representation quality over `PRESENT` elements *only*. A category
  with no `PRESENT` element is undefined and excluded from the aggregate, so
  `VERIFIED_ABSENT` is credited to Coverage but neither rewards nor penalizes
  Fidelity. Coverage and Fidelity are therefore independent.
* **Readiness** — harmonic mean of Coverage and Fidelity, then constrained by the
  foundational conditions (unavailable/low-coverage/low-fidelity foundational
  categories, localization ambiguity, blocking conflicts). A condition may only
  *lower* the level. Composite-SAP Readiness is the minimum over its hunks.

These invariants are locked in by `tests/unit/test_characterization.py`.

## Extending SALP

Add or replace an analyzer for any category:

```python
from salp.analyzers import AnalysisContext, Analyzer, register
from salp.models import Category, CategoryEvidence

@register
class MyRefactoringAnalyzer(Analyzer):
    category = Category.REFACTORING
    component_name = "my-refactoring"
    tool = "RefactoringMiner"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        ...  # return PRESENT / VERIFIED_ABSENT / UNAVAILABLE with provenance
```

Put the module under `analyzers/` and import it from `analyzers/__init__.py` —
that import *is* the registration, and registering a class replaces the built-in
for its category. Build the category through `self.draft(...)` so every required
information element carries an explicit outcome.

### Adding a language

Every structural analysis is written once against the node vocabulary in
[structural/grammars.py](src/salp/structural/grammars.py), not against one
grammar's node names. A language is a `Grammar` declaration plus its tree-sitter
binding in the `structural` extra — no analyzer changes:

```python
KOTLIN = Grammar(
    name="Kotlin",
    module="tree_sitter_kotlin",
    extensions=frozenset({"kt", "kts"}),
    root="source_file",
    class_like=frozenset({"class_declaration", "object_declaration"}),
    method_like=frozenset({"function_declaration"}),
    ...
)
```

Then add it to `GRAMMARS`. Availability is per language: `grammar_for(ext)`
returns None when the binding is not installed, and `diagnostic_for(ext)`
distinguishes "no grammar claims `.kt`" from "install `tree-sitter-kotlin`".
Analyzers record `UNAVAILABLE` with that diagnostic — nothing falls back to
another language's grammar, which is how Scala was once parsed as Java and the
error tree reported as evidence.

The `ControlFlow` vocabulary is language-neutral and part of the written schema;
each grammar maps its own node types onto it, so Scala's `match_expression`
reports as `switch_expression` and its for-comprehension as
`enhanced_for_statement`.

[CONTRIBUTING.md](CONTRIBUTING.md) covers the four rules the evidence model
depends on.

## Status

All eight evidence categories are implemented. Foundational evidence comes from
GACPD; structural, surrounding, compatibility, and verification are recovered
from the whole files and repository trees at their pinned commits; refactoring
runs RefactoringMiner over the target's divergence-to-cutoff drift when
`tools.refactoringminer_jar` is configured, and reports an explicit gap when it
is not.

On the reference sample — 11 SAPs, 25 hunks, 1,450 required information elements:

| Population | Mean Coverage | Readiness |
| --- | --- | --- |
| Java SAPs (23 hunks) | 0.941 | 9 High |
| Scala SAPs (2 hunks) | 0.931 | 2 High |
| All hunks | 0.940 | — |

Per category, across all 25 hunks:

| Category | PRESENT | VERIFIED_ABSENT | UNAVAILABLE | NOT_APPLICABLE |
| --- | ---: | ---: | ---: | ---: |
| source_change | 150 | 0 | 0 | 0 |
| target_localization | 150 | 25 | 0 | 0 |
| function_transformation | 135 | 6 | 0 | 9 |
| compatibility | 163 | 12 | 0 | 0 |
| structural | 149 | 0 | 1 | 0 |
| surrounding | 121 | 20 | 9 | 0 |
| verification | 50 | 0 | 50 | 0 |
| refactoring | 0 | 125 | 0 | 0 |
| standalone | 0 | 150 | 0 | 0 |
| artifact_placement | 0 | 0 | 0 | 125 |

Refactoring is entirely VERIFIED_ABSENT: RefactoringMiner ran over the target's
drift (248 commits, 326 refactorings for the Kafka pair) and none of them touched
the five files these SAPs are about — the landing sites did not move.
`artifact_placement` is `NOT_APPLICABLE` on all 25 hunks (125 elements), every
SAP here being a mapped change, and so leaves both denominators rather than
counting against Coverage.

`function_transformation` has 9 `NOT_APPLICABLE` elements for the same reason at
element scope: nine hunks edit outside any method — eight in an import block, one
in a Scala class parameter list — so τ is undefined there rather than
unrecovered. The single remaining `structural` gap is a target file whose
counterpart method no longer exists in the variant, which is a real finding and
scores as one. All of these are reported as explicit gaps with diagnostics rather
than silently skipped — which is the signal the evidence model exists to give.

Known gaps, in the order they are worth closing:

1. **Minting standalone SAPs** — the change type, its foundational set, and both
   analyzers exist and are tested, but the pipeline only mints MO (mapped) files,
   so no standalone SAP is constructed from a real run yet.
2. **More languages** — Java and Scala are supported; a third is a `Grammar`
   declaration in `structural/grammars.py`, not a change to any analyzer.
3. **Engine hardening** — Coverage iterates the categories it is handed rather than
   the set expected for the change type, so an uninvestigated category is dropped
   from the denominator instead of scoring zero. No `NOT_READY` level, and
   validation errors are logged rather than gating readiness.
4. **Evidence reduction** — relationship edges exist in the index but only the
   `aligned_to` edge is emitted, and the reachability-based reduction over the hunk
   index is not implemented.
5. **Profile completeness** — element-level lists, partial-representation rationale,
   weight/threshold configuration, and `generated_at` are not yet recorded.
6. **Extended validation** — PR-level manifest validation and acyclicity of the
   composite ordering relation. (Pin resolvability now has a real answer to
   validate against.)
7. **Adaptation-goodness metrics** — Difficulty `D`, Context Economy `rho`,
   Verifiability `V`, and the calibration protocol.

The structural and surrounding analyzers are adapted from the prior
implementation, [unlv-evol/GACPD_Hunk_Context_Extraction](https://github.com/unlv-evol/GACPD_Hunk_Context_Extraction);
what was taken, left, and changed is recorded in
[docs/adoption/gacpd-hunk-context-extraction.md](docs/adoption/gacpd-hunk-context-extraction.md).

The GACPD record parsers in `ingest/metadata.py` and `ingest/diffs.py` are confirmed
against a real run (6 pull requests, 11 MO files, two variant pairs, Java and
Scala) and covered by `tests/integration/test_real_gacpd.py`, which skips when no
sample is present. Unmatched fields degrade to `UNAVAILABLE` with a diagnostic,
never to a guess. See [data/gacpd/README.md](data/gacpd/README.md) for the record
formats and the alignment-confidence rule.
