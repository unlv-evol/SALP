# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Characterization over required information elements.** Each category
  declares the elements the specification requires (`elements.py`), with one
  uniform partial-representation rule — `rep(e) = |represented fields| /
  |required fields|`. Coverage and Fidelity are graded rather than
  all-or-nothing.
- **Canonical on-disk layout.** Index and payload are separated: index files
  carry no program text, and payloads are written as raw source files at the
  paths the index references. Output is grouped by variant pair, target-first
  (`data/out/linkedinKafka-apacheKafka/PR-12535/sap-CombinedKey/`).
- **Repository state binding.** `salp fetch-repos` populates a bare-clone cache;
  pull-request heads and cutoff dates resolve to commits over the git wire
  protocol, with no GitHub API calls, token, or rate limit.
- **Structural and surrounding evidence** via tree-sitter, adapted from
  [GACPD_Hunk_Context_Extraction](https://github.com/unlv-evol/GACPD_Hunk_Context_Extraction)
  — see [docs/adoption](docs/adoption/gacpd-hunk-context-extraction.md).
- **Compatibility evidence** from imports and build-file declarations at both
  pinned states; **verification evidence** from target-side test discovery;
  **refactoring evidence** via RefactoringMiner when configured.
- Project infrastructure: CI, pre-commit, contributing guide, coverage config.
- `NOT_APPLICABLE` evidence state, removed from both the Coverage and the
  Fidelity denominator, so a standalone-artifact change is not capped by
  categories it structurally cannot have.
- Standalone-artifact and artifact-placement analyzers.
- Schema conformance of the *written* package (`salp validate`), gated in CI
  alongside an artifact-generation check.
- Tool versions recorded in evidence provenance.
- `ingest.revert_patch` reconstructs a pre-change file from its post-change state
  and unified diff, verifying every context and added line and returning nothing
  on any mismatch.
- `structural.locate_method` finds a method by signature correspondence, in three
  recorded steps — exact signature, name and arity, name alone — so a weaker
  match is visible rather than passed off as an exact one.

- **Scala support.** `structural/grammars.py` declares the node vocabulary per
  language — Java and Scala — and every structural analysis is written against
  it rather than against one grammar's node names, so a third language is a
  `Grammar` declaration, not a change to any analyzer. Availability is checked
  per language: a missing binding is reported by name, and nothing falls back to
  another language's grammar.

### Changed

- `structural/java.py` is now `structural/syntax.py`, and its functions take the
  `Grammar` to use. Entry points in `structural/context.py` take the file's
  extension. There is no default: a caller that does not say which language it
  has cannot silently get Java.
- **A GACPD-only run now characterizes as Low, not Moderate.** τ needs the
  repositories at their pinned states; without them a foundational element is
  `UNAVAILABLE`, which the specification caps at Low. The previous Moderate was
  reported while all three members of τ were stand-ins.

### Fixed

- **τ = (f_s, f'_s, f_t) was not three functions.** `f_s` and `f'_s` were GACPD
  hunk regions in diff syntax — `@@` headers, `-`/`+` prefixes — and `f_t` was
  the whole enclosing file, duplicated into every function-pool entry. All three
  are now sliced out of the files at their pinned repository states.
- **The target function was located by the source's line numbers.** In a diverged
  variant those lines land on whatever occupies them: on the reference sample 4
  of 12 resolved to a different method — `saveNow` to `saveNowInternal`,
  `removeTopicEntryForBroker` to `update` — each reported `PRESENT`. The
  counterpart is now matched by signature.
- `localized_target_function` reported `representation: 1.0` while pointing at a
  whole file. Recovering the file a target function lives in is partial evidence,
  and now scores as such.
- An edit region outside any method — an import block, a class parameter list —
  has no enclosing function by construction. τ is now `NOT_APPLICABLE` there,
  leaving both denominators, instead of scoring the hunk down for evidence it
  cannot have. Only a file that could not be read or parsed stays `UNAVAILABLE`.
- A source method with no counterpart in the target reported `target_structure`
  as `PRESENT` on the strength of file-level context alone. It is now
  `UNAVAILABLE` with the reason; only a region that never had an enclosing method
  is legitimately reported from file structure.

- Blank fields in `pr_results.txt` swallowed the following line, so a PR with no
  title took `"PR Description:"` as its title.
- Alignment confidence fell through to the additions block whenever the deletion
  anchor legitimately scored 0.0.
- Multi-hunk files used only the first hunk's payloads; hunks are now grouped
  into a function pool by diff section heading.
- A pure-deletion hunk has no `full_add`; the missing side is reconstructed from
  the hunk diff instead of reporting an incomplete Transformation Unit.
- Compatibility raised false `BLOCKING_CONFLICT` findings against each project's
  own packages. First-party imports are now excluded, and the flag is no longer
  raised from a textual scan of build files at all — that cannot establish an
  irreconcilable conflict.
- Evidence output was not reproducible: nested constructs share a start point, so
  sorting on it alone left ties to capture order.
- The surrounding analyzer parsed non-Java files with the Java grammar.
- Refactoring results correlated left and right locations by index
  unconditionally; on a real run half the reports have arrays of different
  lengths, so pairing is now applied only where it holds.
- RefactoringMiner never ran on Windows: the configured path named the Unix
  launcher, and the distribution's `.bat` was ignored. The Windows launcher is
  now derived from the configured path rather than configured separately, so one
  config file stays correct on every platform that shares it.
- An unknown key under `tools` was accepted and ignored. A renamed or misspelled
  setting silently turned a whole evidence category `UNAVAILABLE` — on the
  reference sample that moved `refactoring` from 125 `VERIFIED_ABSENT` to 125
  `UNAVAILABLE` and mean Coverage from 0.940 to 0.823, with nothing naming the
  cause. `tools` now rejects unknown keys.

### Changed

- `src/salp/` reorganised into seven subpackages layered by pipeline stage —
  `models`, `ingest`, `repos`, `structural`, `analyzers`, `characterization`,
  `packaging` — each exposing one public surface through its `__init__`. It
  passed through a flat 15-module intermediate on the way; the layering rules are
  in [CONTRIBUTING](CONTRIBUTING.md).
- `LICENSE` replaced with the full Apache-2.0 text; it was a two-line
  placeholder while the package metadata already declared the licence.

### Known gaps

Tracked under "Status" in the [README](README.md): standalone change type, a
Scala grammar, engine hardening (`NOT_READY`, validity gating), evidence
reduction, profile completeness, extended validation, and the
adaptation-goodness metric families.

[Unreleased]: https://github.com/unlv-evol/SALP/commits/main
