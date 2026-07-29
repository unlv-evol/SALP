# Repository clone cache

Bare clones of the source (mainline) and target (divergent) repositories named
by the GACPD output. Contents are git-ignored; only this README and `.gitkeep`
are tracked.

```bash
salp -c configs/default.yaml fetch-repos --dry-run   # what would be fetched
salp -c configs/default.yaml fetch-repos             # clone + fetch PR heads
```

`fetch-repos` is the pipeline's **only** network operation. `salp run` reads this
cache locally and never fetches, so construction stays deterministic.

## Why clones are needed

GACPD gives the whole *target* file but only *regions* of the source, no history,
no build files, and dates instead of commit SHAs. Everything still unrecovered
depends on the repositories:

| Need | Requires |
| --- | --- |
| Repository-state binding | resolving a date and a PR number to commits |
| Structural evidence | the whole source file: enclosing class, package, imports |
| Refactoring evidence | git history — RefactoringMiner diffs commit pairs |
| Compatibility evidence | imports plus `pom.xml` / `build.gradle` |
| Verification evidence | the target tree, to find tests covering the edit region |
| Surrounding context | both trees, for callers, callees, and neighbours |

## No API calls

Everything resolves over the git wire protocol, so there is no REST dependency,
no token, and no rate limit:

| What | How |
| --- | --- |
| Source commit | `git fetch origin refs/pull/<n>/head` — a git ref, not a REST call |
| Target commit | `git rev-list -1 --before=<cutoff> HEAD` |
| File at a pin | `git show <sha>:<path>` |

PR title, description, URL, and dates already come from `pr_results.txt`, so the
API would add nothing.

## Layout

```
data/repos/
  apache__kafka.git/            # owner/name -> owner__name.git
  linkedin__kafka.git/
  bitcoinj__bitcoinj.git/
  langerhans__dogecoinj-new.git/
```

Clones are **bare** and carry **full history**.

*Bare* because SALP never needs a working tree: file contents are read with
`git show <sha>:<path>`, and RefactoringMiner reads trees through JGit. A
checkout per pinned state would be pure duplication.

*Full history* because both resolutions depend on it — RefactoringMiner diffs
commit pairs, and a cutoff date is resolved by walking back through the log. A
`--depth` shallow clone breaks both.

Fetched pull-request heads are kept under `refs/salp/pr/<n>` so they survive
later fetches and stay visible to `git for-each-ref`.

## Degrading without a clone

A missing clone is an information gap, never an error. The pin stays date-based,
records why, and the affected element is partially represented:

```json
{ "repo": "apache/kafka", "commit": null,
  "diagnostics": "no local clone of apache/kafka; run `salp fetch-repos` to bind this SAP to a commit" }
```

That costs Fidelity on `source_repo_revision` and `target_repo_revision` — they
score 0.5 rather than 1.0, having recovered the repository but not the revision.
Resolution can also be turned off outright with `resolve_pins: false` or
`salp run --no-resolve-pins`.

## Disk

Full history for large projects is not small; budget roughly a gigabyte or two
for the four repositories of the current sample. Re-running `fetch-repos` is
incremental: existing clones are refreshed rather than re-cloned, and
pull-request heads already present are skipped.
