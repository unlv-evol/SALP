# Contributing to SALP

## Setup

```bash
make dev      # editable install with dev + structural extras, plus pre-commit hooks
make check    # ruff, mypy, pytest — exactly what CI runs
```

Run `make help` for everything else.

Working against the real benchmark needs two git-ignored directories, both
documented in their own READMEs:

```bash
# 1. drop a real GACPD run under data/gacpd/
make fetch    # clone the source/target repositories it names (the only network step)
make run      # construct SAPs into data/out/
```

Tests that need the sample skip themselves without it, so `make check` passes on
a clean clone. To run only the fast suite: `pytest -m "not integration"`.

`make check` does not validate written packages, because that needs a run. CI has
a separate **conformance** job that generates a run, checks the expected
artifacts exist, and then checks them against the schema. Locally:

```bash
make conformance    # = make run && make validate
salp validate --output data/out    # or just the check, on an existing run
```

`salp validate` exits non-zero on any finding, so CI gates on it. It checks that
every required file is present, every index reference resolves, every object
identifier is unique, every evidence object carries a valid state and provenance,
and the composite hunk ordering is total.

Refactoring detection is the slowest analysis and is cached under
`data/repos/.refactoring-cache/`. `salp run --no-refactorings` skips it while
iterating on something else.

## Layout

`src/salp/` is layered by pipeline stage — see the tree in the
[README](README.md). Two rules keep it navigable:

**One public surface per package.** Import from `salp.repos`, not
`salp.repos.cache`. Each `__init__.py` declares `__all__`; add a name there when
it becomes part of the package's API, and leave it out when it is internal.

**Dependencies run one way.** `models` depends on nothing; `ingest`, `repos`, and
`structural` depend on `models`; `analyzers` depends on those; `characterization`
and `packaging` sit above them; `pipeline` wires everything together. A new
import that runs against that direction is a design smell — the shared thing
probably belongs in `models`.

## Adding an analysis

Every evidence category is produced by one analyzer, discovered through a
registry — the orchestrator never needs editing.

```python
@register
class MyRefactoringAnalyzer(Analyzer):
    category = Category.REFACTORING
    component_name = "my-refactoring"
    tool = "RefactoringMiner"
    version = "3.0.9"

    def investigate(self, ctx: AnalysisContext) -> CategoryEvidence:
        d = self.draft(ctx, "not investigated")
        ...
        return d.build()
```

Four rules the evidence model depends on:

1. **Build through `self.draft(...)`.** It seeds every required element of the
   category as `UNAVAILABLE`, so an element you forget stays an explicit gap with
   a diagnostic instead of vanishing from the Coverage denominator.
2. **Pick the right state.** Four exist, and they are not interchangeable:

   | State | Meaning | Coverage | Fidelity |
   | --- | --- | --- | --- |
   | `PRESENT` | recovered | resolved | scored |
   | `VERIFIED_ABSENT` | looked for, confirmed absent | resolved | excluded |
   | `UNAVAILABLE` | could not determine | 0 | excluded |
   | `NOT_APPLICABLE` | the question does not arise for this change type | excluded | excluded |

   Confusing `VERIFIED_ABSENT` with `UNAVAILABLE` is called out in the
   specification as a common representation mistake. `NOT_APPLICABLE` is set
   centrally by the builder from `CategorySpec.applicable_to` — an analyzer
   should not normally return it itself.
3. **Partial recovery is `PRESENT` with a representation below 1.0**, not a
   failure. Declare the fields an element needs in `elements.py` and the
   fraction is computed for you.
4. **Record the tool version.** Override `tool_version()` to report the version
   actually installed rather than a hardcoded string; evidence is reproducible
   only under fixed tool versions and pinned repository states.
5. **A missing tool is a gap, not an error.** Return `UNAVAILABLE` with a
   diagnostic naming the fix; never raise. One analyzer must not abort a run.

Reserve `blocking_conflict` for a concrete obstacle you can actually
demonstrate — it caps Readiness at Low regardless of Coverage and Fidelity.

## Conventions

- Line length 100; ruff (`E,F,I,UP,B,SIM`) and mypy `strict` both gate CI.
- Public functions carry docstrings explaining *why*, not what.
- Anything read from an external tool or repository gets provenance recorded.
- Evidence output must be deterministic. Sort on a total key: a tie broken by
  iteration order has already caused a reproducibility bug here once.

## Filing issues

Issue forms live in `.github/ISSUE_TEMPLATE/`, and the two SALP-specific ones
cover most of what comes up:

- **Evidence gap** — an analyzer reports the wrong evidence state. Every element
  carries a `provenance.diagnostics` string explaining its state; quoting it is
  usually enough to locate the cause.
- **Parser gap** — GACPD emitted a record the ingester does not handle. Paste the
  raw excerpt: these records use CRLF endings and may leave fields blank, and
  both have caused real bugs that do not survive being retyped.

Anything else goes to **Bug report** or **Feature or enhancement**.

## Tests

- `tests/unit/` — no network, no `data/gacpd/`, no clones.
- `tests/integration/` — runs against the real sample; marked `integration` and
  skipped when it is absent.

New behaviour needs a test that fails without it. Corrections to adapted code
need a test naming the correction — see `tests/unit/test_structural.py`.
