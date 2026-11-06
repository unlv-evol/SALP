# GACPD sample

Real GACPD output for two pull requests, and the repository files SALP reads
while building their SAPs. `tests/integration/test_real_gacpd.py` runs the whole
pipeline over it, so the integration suite needs nothing outside the repository
except `git`.

| Pull request | Source → target | MO files |
|---|---|---|
| 2785 | `bitcoinj/bitcoinj` → `langerhans/dogecoinj-new` | `MnemonicCode`, `MnemonicCodeTest`, `DeterministicSeed` |
| 12189 | `apache/kafka` → `linkedin/kafka` | `DeleteTopicsRequestTest` (plus an NA sibling) |

## `gacpd/`

The GACPD run, keeping only what the ingester reads: `pr_results.json`,
`results.json`, the patch, `cmp/`, and each hunk's `full_del`, `full_add` and
`context`. Dropped: `reports/`, `.jscpd.json`, `hunk_*_additions/deletions`, the
`NE/` bucket, an NA file's own hunks, and `PlaintextAdminIntegrationTest` (about
390 KB, whose one distinct case -- two hunks in one method -- is covered by
`test_hunks_in_the_same_function_share_one_pool_entry`).

## `repos/`

One directory per repository, holding the files read at its pinned state. The
test fixture commits each into a bare repository dated `2000-01-01`, before every
cutoff, and points `refs/salp/pr/<n>` at it for the pull requests listed in
`pull_requests.json`.

Two kinds of file are excerpts rather than copies, because only part of them is
ever read:

- **`build.gradle`** keeps only the lines matching `_GRADLE_DEP` in
  `salp/repos/files.py`, which is single-line. An empty one declared nothing
  inline, and still marks its module as a build root.
- **Test files found by `grep_files`** but never read keep only the lines
  containing a searched name. Every hit is reproduced, and nothing new can match.

Everything else, including every source file the pipeline parses, is verbatim.

## Regenerating

Run the pipeline over `gacpd/` against full clones (`salp fetch-repos`), record
the paths `git show` reads and `git grep` returns, and copy them out under the
rules above. Check the result by running the pipeline twice -- full clones, then
this snapshot -- and diffing the output: only commit SHAs may differ.

`.gitattributes` keeps every byte as written: GACPD's CRLF endings are part of
what these tests guard.
