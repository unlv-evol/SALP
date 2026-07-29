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

### Fixed

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
