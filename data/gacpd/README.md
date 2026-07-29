# GACPD run samples

Drop real GACPD output here. This is the default `paths.gacpd_run` in
`configs/default.yaml`, so a run needs no flags:

```bash
salp -c configs/default.yaml run
```

Contents are git-ignored — real runs carry whole source trees and are far too
large to commit. Only this README and `.gitkeep` are tracked.

`tests/integration/test_real_gacpd.py` runs against whatever is here and skips
when the directory is empty, so the sample doubles as a regression suite for the
parsers.

## Layout

The ingester (`src/salp/ingest/gacpd.py`) discovers every `*_MO` pull-request
directory beneath this folder:

```
data/gacpd/
  <mainline>-<divergent>/               # e.g. apache_kafka-linked_kafka
    <PR-number>_MO/
      pr_results.txt                    # PR identity, dates, per-file summary
      MO/                               # mapped files -> one SAP each
        <flattened source path>/        # streams_src_main_..._CombinedKey_java
          results.txt                   # repo pair, paths, similarity, class.
          cmp/<File>.<ext>              # f_t: the divergent-repository file
          src/<File>.patch              # whole-file unified diff
          src/hunk_<n>_full_del.<ext>   # @@ header + pre-change region
          src/hunk_<n>_full_add.<ext>   # @@ header + post-change region
          src/hunk_<n>_context.<ext>    # context lines only
          src/hunk_<n>_additions.<ext>  # added lines only
          src/hunk_<n>_deletions.<ext>  # deleted lines only
          .jscpd.json, reports/         # clone-detector output, excluded
      NA/  ED/                          # retained as _context/, never SAP roots
```

One SAP is minted per file classified **MO**. `NA` and `ED` files are never SAP
roots; they are retained as context payloads under the pull request's
`_context/` and referenced by that pull request's MO SAPs.

The run directory name is **not** a reliable source for the repository pair — it
abbreviates (`linked_kafka` for `linkedin/kafka`) and its separator is ambiguous
when a repository name itself contains a hyphen (`langerhans_dogecoinj-new`).
The pair is read from `results.txt` and promoted to the pull request.

## Record formats

Parsed by `src/salp/ingest/records.py`; the discovery walk that finds them is
`src/salp/ingest/gacpd.py`, and the hunk artifacts are parsed by
`src/salp/ingest/diffs.py`.

Both records use **CRLF** line endings and may leave any field blank. A blank
field must not swallow the following line, so values are matched with horizontal
whitespace only and an empty value degrades to `None` plus a diagnostic.

### `pr_results.txt` (per pull request)

```
Classified PR: 12535
PR Title: KAFKA-13769 Fix version check in SubscriptionStoreReceiveProcessorSupplier
PR Description: <free text, may span lines>
PR Location: https://github.com/apache/kafka/pull/12535
REPO DIVERGENCE DATE: 2022-06-02T00:00:00Z
CUTOFF DATE: 2022-12-02T23:59:59Z
```

It does **not** name the divergent repository. PR 2731 in the current sample
leaves `PR Title`, `PR Description`, and `PR Location` blank.

### `results.txt` (per file)

```
In PR: 12535
Mainline is: apache/kafka
Divergent Repo is: linkedin/kafka
File: streams/src/main/java/.../CombinedKey.java
Is called in Divergent Path is: Results/Repos_files/<run>/linkedin/kafka/streams/.../CombinedKey.java
Similarity Check:
src/hunk_1_additions.java (50) - has a similarity of: 0%
src/hunk_1_deletions.java (30) - has a similarity of: 100%
Classification:
The final classification is: MO
```

The located path is prefixed with GACPD's own working directory; everything up
to and including `<owner>/<repo>/` is stripped to leave the repository-relative
path.

### Similarity and alignment confidence

The clone detector is run at descending minimum-token thresholds (50, 40, 30,
20) and each block is reported separately. The **deletions** block is the
anchor: its presence in the target is what localization asserts. The
**additions** block is the replacement, which is not expected to exist in the
target yet — a 0% additions similarity is the divergence the adaptation must
reconcile, not a localization failure.

Alignment confidence is the mean deletion similarity across the thresholds
reported, falling back to the additions block only for pure insertions that have
no deletion anchor. A block matching at every threshold scores 1.0; one matching
only at the coarsest threshold scores proportionally less. `CombinedKey` H-1
scores 1/3, which is how a genuine but weak alignment is recorded as *reduced
confidence* rather than as a failure.

> An MO classification asserts successful mapping, not high similarity. Low
> confidence must never cap Readiness on its own.

### Hunk artifacts

`hunk_<n>_full_del` and `hunk_<n>_full_add` lead with the unified-diff header
for that hunk:

```
@@ -513,6 +516,10 @@ public void close() throws Exception {
```

Two things come from that header without parsing any source: the **edit-region
spans**, and the **enclosing function** from the section heading. Hunks sharing a
heading occupy the same function and share one function-pool entry — the five
hunks of `KafkaClusterTestKit.java` collapse onto three functions.

Two cautions, both handled:

- The heading is git's best-effort guess and is sometimes a truncated
  continuation line (`metaProperties, config, new MetadataRecordSerde(), ...`).
  A pool entry is named only when the heading opens with declaration modifiers;
  otherwise it stays positional (`KafkaClusterTestKit_fn1`).
- A pure-deletion hunk has no `full_add` and a pure insertion no `full_del`,
  because there was nothing to write. Both sides are reconstructed exactly from
  the hunk diff, with the derivation recorded in provenance.

These artifacts cover the changed region and its diff context, **not** whole
function bodies. Expanding a region to true function boundaries needs the
structural analysis, so payloads built from them carry a diagnostic saying so.

## Re-checking the parsers after adding a sample

```bash
PYTHONPATH=./src python -c "
from pathlib import Path
from salp.ingest import discover_pull_requests
for pr in discover_pull_requests(Path('data/gacpd')):
    m = pr.metadata
    print(f'{pr.pr_id} #{m.number} {m.source_repo} -> {m.target_repo} {m.divergence_date}..{m.cutoff_date}')
    for d in m.diagnostics: print('   ! ' + d)
    for f in pr.mo_files:
        print(f'   {f.display_name} ({f.ext}) hunks={len(f.hunks)} -> {f.localization.divergent_path}')
        for h in f.hunks:
            print(f'      {h.hunk_id} conf={f.localization.confidence(h.hunk_id)}')
"
```

A field the parser misses degrades to an `UNAVAILABLE` element with a
diagnostic, never to a guess — wrong patterns show up as depressed Coverage
rather than as silently fabricated evidence.
