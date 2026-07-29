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
                   ├ structural  ───▶ structural/       (tree-sitter Java)
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
tree-sitter, without which the structural, surrounding, and compatibility
analyzers all report `UNAVAILABLE`. A run still succeeds, but on the reference
sample mean Coverage over all 25 hunks falls from 0.805 to 0.666 and **no package
reaches High Readiness** — all eleven cap at Moderate.

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
| `tools/` | RefactoringMiner, tree-sitter grammars, other external binaries | [README](tools/README.md) |

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

  structural/             # tree-sitter Java
    java.py               #   parsing, navigation, signatures
    context.py            #   locating an edit region's context
    metadata.py           #   class/method/control-flow metadata

  analyzers/              # one investigation per evidence category
    base.py               #   contract, draft, registry
    gacpd.py              #   source change, localization, transformation
    structural.py         #   structural + surrounding
    compatibility.py      #   APIs and dependencies
    verification.py       #   target-side oracle discovery
    refactoring.py        #   RefactoringMiner
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

* **Coverage** — investigation completion over *required* categories. `PRESENT` and
  `VERIFIED_ABSENT` are resolved (1); `UNAVAILABLE` is 0. Optional/conditional
  categories never reduce Coverage.
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

[CONTRIBUTING.md](CONTRIBUTING.md) covers the four rules the evidence model
depends on.

## Status

All eight evidence categories are implemented. Foundational evidence comes from
GACPD; structural, surrounding, compatibility, and verification are recovered
from the whole files and repository trees at their pinned commits; refactoring
runs RefactoringMiner when `tools.refactoringminer_jar` is configured and reports
an explicit gap when it is not.

On the reference sample — 11 SAPs, 25 hunks, 1,175 required information elements:

| Population | Mean Coverage | Readiness |
| --- | --- | --- |
| Java SAPs (23 hunks) | 0.824 | 9 High |
| Scala SAPs (2 hunks) | 0.588 | 2 Moderate |
| All hunks | 0.805 | — |

Per category, across all 25 hunks:

| Category | PRESENT | VERIFIED_ABSENT | UNAVAILABLE |
| --- | ---: | ---: | ---: |
| source_change | 150 | 0 | 0 |
| target_localization | 150 | 25 | 0 |
| function_transformation | 144 | 6 | 0 |
| structural | 138 | 0 | 12 |
| surrounding | 114 | 18 | 18 |
| compatibility | 135 | 26 | 14 |
| verification | 50 | 0 | 50 |
| refactoring | 0 | 0 | 125 |

Refactoring is entirely UNAVAILABLE because no RefactoringMiner jar is installed;
the 12 structural gaps and both Moderate packages are the two Scala files, which
have no configured grammar. Both are reported as explicit gaps with diagnostics
rather than silently skipped — which is the signal the evidence model exists to
give.

Known gaps, in the order they are worth closing:

1. **Standalone change type** — `F = {artifact-source, artifact-placement}` is only
   half-modelled; no standalone SAP is ever constructed.
2. **A Scala grammar** — two of the eleven sample SAPs cap at Moderate purely for
   want of one.
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
