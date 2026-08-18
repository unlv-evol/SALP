# Adoption: GACPD_Hunk_Context_Extraction

Source: [unlv-evol/GACPD_Hunk_Context_Extraction](https://github.com/unlv-evol/GACPD_Hunk_Context_Extraction)
Reviewed at `main`, 20 files / ~2,460 lines of Python.
Adopted: 2026-07-28.

That repository is the prior implementation of hunk-context extraction over
GACPD output. Its analysis logic is correct and was reused rather than rewritten;
what changed is the surrounding architecture, because SALP is a library with an
evidence model rather than a script with a config module.

This document records what was taken, what was left, and every place the
behaviour was deliberately changed.

---

## 1. What was adopted

### 1.1 `Making_AST/Extract_Hunk_AST_Util.py` → `src/salp/structural/syntax.py`

Tree-sitter navigation. The single most reusable asset in the repository. The
adopted functions were Java-only; here they take the `Grammar` to use, so the
same logic serves Scala (§7).

| Original | Here | Notes |
| --- | --- | --- |
| `get_context_parent_class` | `enclosing_class` | widened past `class_declaration` |
| `get_context_parent_method` | `enclosing_method` | widened to constructors |
| `is_context_in_method` | `is_in_method` | |
| `get_method_parameter_type` | `parameter_type` | |
| `get_method_signature` | `method_signature` | |
| `get_node_exact_string` | `node_text` | **corrected**, see §3.1 |
| `sort_nodes_by_start_point` | `sort_by_position` | |
| `Construct_Flow_Type` (enum) | `ControlFlow` (StrEnum) | values read as Java node types; now a language-neutral vocabulary each grammar maps onto |
| `get_node_construct_flow_type` | `control_flow_of` | table lookup + else/else-if disambiguation |
| `is_hunk_import` | `is_import_region` | **corrected**, see §3.4 |

### 1.2 `Making_AST/Extract_Hunk_AST.py` → `src/salp/structural/context.py`

| Original | Here | Notes |
| --- | --- | --- |
| `find_context_node` | `locate` | returns a typed `EditContext`; adds overlap fallback (§3.3) |
| `context_node_to_dict` | `to_ast_dict` | text omitted by default (§3.5) |
| `get_import_hunk_source_code` | `imports_of` | whole-file imports, not just those adjacent to the hunk |
| `get_current_AST_import_declarations_classes` | `imported_class_names` | de-globalised (§3.2) |

### 1.3 `Making_AST/Extract_Hunk_Metadata.py` → `src/salp/structural/metadata.py`

Maps almost one-to-one onto the required information elements of the
**Structural Context** and **Surrounding Program Context** categories.

| Original | Here | Feeds |
| --- | --- | --- |
| `extract_class_information` | `class_structure` | `surrounding.enclosing_context` |
| `extract_package_information` | `EditContext.package` | `surrounding.file_module_context` |
| `extract_imported_libraries` | `imports_of` | `compatibility.source_apis` |
| `extract_called_methods` | `invoked_methods` | `surrounding.callers_callees` |
| `extract_referenced_classes` | `referenced_classes` | `surrounding.related_entities` |
| `extract_neighboring_methods_within_same_class` | `neighboring_methods` | `surrounding.callers_callees` |
| `extract_control_flow_constructs` | `control_flow_constructs` | `structural.edit_region_structure` |
| `generate_structured_context_metadata` | `describe` | assembly |

### 1.4 `Refactoring_Detection/Retrieve_Before_PR.py`

| Original | Here | Notes |
| --- | --- | --- |
| `get_methods_in_hunk` | `structural/context.py::methods_overlapping` | the overlap predicate `max(starts) <= min(ends)`, and the 0-based/1-based shift |
| `get_file_at_commit` | `repos/files.py::read_file` | converged independently; see §4 |

### 1.5 `Refactoring_Detection/Refactoring_History_Construction.py`

Per-file filtering of a RefactoringMiner report. Adopted in
`analyzers/refactoring.py`.

| Original | Here | Notes |
| --- | --- | --- |
| filter locations by `filePath` | `Location.names` | matches full path or basename |
| left/right index correlation | `entity_mapping` | **conditional**, see §3.9 |
| retain `repository`, `sha1`, `url` per commit | `relevant_refactorings` | keeps a finding traceable to its revision |
| retain `markup` | `relevant_refactorings` | RefactoringMiner's highlighted description |
| `Rename File` skipped | `FILE_LEVEL` | **not** skipped, see §3.10 |

### 1.6 `Refactoring_Detection/Refactoring_Detection.py`

| Original | Here | Notes |
| --- | --- | --- |
| `get_last_commit_before_date` | `repos/pins.py::PinResolver.target` | converged independently; see §4 |
| `get_result_important_dates` | `ingest/records.py` | superseded by the fuller parser |
| `get_refactorings_between_commits` | `tools.py::run_refactoring_miner` | the `-bc` form; see §5 |
| `get_refactorings_in_PR_from_remote` | not used | needs a token; the `-bc` form does not |

---

## 2. What was **not** adopted, and why

**`API-Detection/Detect_API.py`** — incomplete. `get_file_API` returns `""`,
`get_file_imports` is `pass`, the Gradle subprocess call is commented out, and
the module carries hardcoded absolute Windows paths
(`C:/Users/pahla/...`). Only the *approach* is reusable, recorded in §5.
`global_deps.init.gradle` is reusable verbatim and is noted there.

**`GACPD_Output_Processing/GACPD_Result_Processing.py`** — superseded.
`src/salp/ingest/` already parses more than this does: per-block and
per-threshold similarity, the repository pair, both paths, and CRLF/blank-field
handling. Its `get_file_result_similarity_scores` *did* provide a useful
confirmation, see §4.

**`Main.Py` and `Config.py`** — different architecture. SALP configures through
YAML validated by pydantic, discovers analyzers through a registry, and treats a
missing tool as an `UNAVAILABLE` evidence state rather than a skipped branch.

**GitHub token / `github-oauth.properties`** — deliberately avoided. SALP
resolves everything over the git wire protocol, so no REST calls, no token, no
rate limit. See `data/repos/README.md`.

---

## 3. Deliberate behaviour changes

### 3.1 Exact text extraction *(bug fix)*

The original sliced by line and column:

```python
exact_string = node_lines[0][start_col:end_col + 1]      # over-reads by one
```

tree-sitter end points are **exclusive**, so the `+ 1` includes one extra
character on every single-line node. `node_text` slices `start_byte:end_byte`,
which is exact and also correct for multi-byte characters. Locked in by
`test_node_text_is_exact_and_not_off_by_one`.

### 3.2 No module-level parser state *(robustness)*

The original stored the working tree in a module global
(`Extract_Hunk_AST.current_generated_AST`) that `get_current_AST_import_declarations`
and its callers read implicitly. Any interleaving of two files would silently
attribute one file's imports to the other. Every function here takes its tree as
a parameter. Locked in by `test_parsing_is_not_shared_between_files`.

### 3.3 Methods by overlap, not containment *(correctness)*

`find_context_node` uses `named_descendant_for_point_range`, which returns the
smallest node *containing* the range. An edit region crossing a method boundary
is contained only by the class body, so the method came back empty — this
happened on the real `CombinedKey.java` sample, whose region spans lines 38–45
across two methods.

The fix reuses the original's own `get_methods_in_hunk` predicate from a
different file: select every method whose range *intersects* the region. `locate`
now falls back to that, reports how many methods the region spans, and lists them
all in `overlapping_methods`. Locked in by
`test_a_region_crossing_methods_selects_by_overlap`.

### 3.4 Import-region detection *(bug fix)*

The original returned `True` for a range of only blank and comment lines, because
its loop `continue`s past them and falls through to `return True`. It also mixed
`line_content_stripped.startswith('import')` with the unstripped
`line_content.startswith('package')`, so an indented `package` failed. Here a
region must contain at least one actual `import`/`package` line to qualify.

### 3.5 Direct-member comparison *(bug fix, ours)*

Porting `captured_field.parent != class_body_node` as `node.parent is body`
silently broke: tree-sitter returns a **fresh wrapper object** on every `.parent`
access, so identity comparison is always false and every class came back with no
fields or methods. `_is_direct_member` compares the stable `node.id`. The
original's `!=` was correct — tree-sitter implements `__eq__`. Locked in by
`test_class_structure_lists_direct_members_only`.

### 3.6 Typed structures and snake_case keys *(convention)*

Free-form dicts with title-cased keys (`"Encapsulating Class Information"`,
`"Invoked Methods"`) become dataclasses with snake_case fields, matching the rest
of the SAP schema and letting mypy check the evidence-construction path.

### 3.7 Signatures use parameter types *(carried over, made explicit)*

`method_signature` reports parameter *types*, not names — so a signature is
stable across a parameter rename and comparable between variants. That was the
original's behaviour; it is now the documented contract and is tested.

### 3.9 Index correlation applied only where it holds *(correctness)*

The reference implementation treats `leftSideLocations` and `rightSideLocations`
as parallel arrays and reads the right side at the left side's index:

```python
relevant_right_side_locations = [refactoring['rightSideLocations'][i] for i in relevant_indices]
```

A real run over `linkedin/kafka` (248 commits, 326 refactorings) shows this holds
for only half the data: **162 of 326** have arrays of *different* lengths. An
Extract Method reported 18 left and 22 right locations — those are all the
locations on each side, not counterparts. For one-to-one refactorings such as
Rename Method the correlation is real and useful.

`entity_mapping` therefore pairs by position **only when the arrays agree in
length**, and otherwise reports each location unpaired with `correlated: false`.
An unmapped finding is more useful than an invented correspondence. Locked in by
`test_mismatched_location_arrays_are_not_paired`.

The original also carries an indexing bug in the same block: it builds a
*compacted* list of matching locations, then indexes it with positions from the
*original* array —

```python
for relevenat_index in relevant_indices:
    side_pair_info = {"Left Side": relevant_left_side_locations[relevenat_index], ...}
```

— which raises `IndexError` whenever the first match is not at position 0.

### 3.10 File-level moves are the most relevant refactorings, not the least

The original skips them (`'found a case of rename file, not handling it though'`).
For a reusable change a Rename/Move File or Class relocates the *landing site
itself*, which is precisely what adaptation must reconcile. `FILE_LEVEL` marks
them, and they produce a `landing_site_relocated_by` edge rather than the
ordinary `landing_site_refactored_by`.

### 3.8 Graceful degradation *(architecture)*

tree-sitter is the optional `structural` extra. Its absence sets
`TREE_SITTER_AVAILABLE = False`, and the analyzer records `UNAVAILABLE` with a
diagnostic instead of raising — an unrun investigation is an information gap, not
an error. The same applies per language: a `.scala` file gets an explicit
`UNAVAILABLE` naming the missing grammar rather than a silent skip.

---

## 4. Independent convergence

Three things were already implemented here before this review, and the reference
implementation agrees. Recording them because agreement is evidence the reading
of GACPD's output is right:

1. **Date → commit resolution.** Theirs: `git log -1 --before=<date> --format=%H`.
   Ours: `git rev-list -1 --before=<cutoff> HEAD`. Equivalent.
2. **File at a commit.** Both read the blob directly rather than checking out.
3. **The `(50)` in a similarity line is a token threshold, not a percentage.**
   Their `get_file_result_similarity_scores` names that captured group
   `token_size`, confirming the interpretation behind our alignment-confidence
   rule (`data/gacpd/README.md`).

---

## 5. Wired since

### Refactoring detection — adopted and running

The RefactoringMiner command shapes were taken verbatim from
`Refactoring_Detection.py` and now live in `src/salp/analyzers/tools.py`:

```
# between two commits, into a JSON report
<RM> -bc <repo_path> <start_sha> <end_sha> -json <out.json>

# every refactoring in a pull request, from the remote
<RM> -gp <git_url> <pr_number> <timeout> -json <out.json>
```

The `-bc` form was chosen: both endpoints are already resolved as repository
state pins, so it needs no token. RefactoringMiner 3.1.4 ships as a distribution
(`bin/` launcher plus `lib/`), not a fat jar, so `tools.refactoringminer_jar`
points at the launcher and the version is read from the distribution directory
name. The distribution carries a `.bat` alongside it, which is selected
automatically on Windows.

One addition of our own: the runner is cached on its arguments. Every pull
request of a variant pair shares one divergence-to-cutoff range, so an uncached
runner re-analyses the same history once per pull request — six runs over three
distinct ranges on the reference sample, which took 2m45s against 58s cached. One change — the original passes `check=True`,
which raises on a non-zero exit; `run_refactoring_miner` returns the stderr as a
diagnostic string instead, and the analyzer records it as UNAVAILABLE. An
unconfigured or failing tool is an information gap, not a pipeline error.

### API and dependency compatibility — partly adopted

`CompatibilityAnalyzer` reads dependency *coordinates* straight out of the build
files at each pinned state (`repos.read_dependencies`), which needs no Gradle
invocation and therefore no checkout. That covers which dependencies are
declared; it does not cover *resolved* versions, which still needs the approach
below.

`API-Detection/global_deps.init.gradle` is reusable as-is for that. It registers a
`ProjectDependencyAnalysis` task on every subproject and writes a dependency
report:

```
./gradlew ProjectDependencyAnalysis -I global_deps.init.gradle -PoutputFile=<path>
```

`determine_line_category` in `Detect_API.py` is a usable sketch of how to parse
that report's tree output (`|`, `+`, `\` prefixes are dependency lines; the rest
are subproject headers), but the parser around it is unfinished.

Running Gradle requires a checkout, which the bare-clone cache deliberately does
not provide — so this needs either a temporary worktree or, better, parsing the
build files directly at the pinned commit.

---

## 6. Where the adopted code lives

```
src/salp/structural/
  java.py       <- Extract_Hunk_AST_Util.py
  context.py    <- Extract_Hunk_AST.py + get_methods_in_hunk
  metadata.py   <- Extract_Hunk_Metadata.py

src/salp/analyzers/
  tools.py            <- Refactoring_Detection.py command shapes
  structural.py       StructuralAnalyzer, SurroundingAnalyzer: locate() + describe()
  compatibility.py    imports_of() + repos.read_dependencies()
  refactoring.py      tools.run_refactoring_miner()

tests/unit/test_structural.py            behaviour, including every correction above
tests/unit/test_enrichment_analyzers.py  the three tool-backed analyzers
```

(Module paths have moved twice since adoption — flattened, then layered into
subpackages. The mapping above is current; see the README for the layout.)

## 7. Effect on the benchmark

Over the 11 SAPs of the current sample, across the three waves of adoption
(tree-sitter context, then compatibility and verification, then RefactoringMiner):

| Category | before | after |
| --- | --- | --- |
| structural | 150 U | 138 P / 12 U |
| surrounding | 24 P / 125 U | 114 P / 18 VA / 18 U |
| compatibility | 175 U | 149 P / 12 VA / 14 U |
| verification | 100 U | 50 P / 50 U |
| refactoring | 125 U | 125 VA |

Coverage rose from 0.529 to **0.940** across all 25 hunks, and Readiness from
uniformly Moderate to **11 High**.

Two things were added after the waves above, and both changed these numbers.

*The function transformation.* The same parser slices the three members of
τ = (f_s, f'_s, f_t) out of whole files and matches the target function to the
source by signature. `locate` supplies the enclosing method on the source side;
`locate_method` — which has no counterpart in the reference implementation —
finds the corresponding method on the target side.

*Scala.* The reference implementation is Java-only, and so was this until the
node vocabulary was lifted into `grammars.py`. Scala names almost every construct
differently — `compilation_unit` for `program`, `function_definition` for
`method_declaration`, `if_expression` for `if_statement`, `template_body` for
`class_body` — and models as expressions what Java models as statements, so the
adopted queries could not simply be reused. Two Scala constructs have no Java
counterpart and are mapped onto the nearest term: a `for` comprehension reports
as `enhanced_for_statement`, and `match` as `switch_expression`.

One adopted assumption did not survive the move. `method_signature` accumulated
children until it reached a `block`, which is the body of every Java method. A
Scala body is any expression — `def size: Int = 1` has an integer literal for a
body — so accumulation now stops at whatever the grammar marks as the `body`
field, which serves both languages.

Refactoring is wholly VERIFIED_ABSENT rather than PRESENT, which is the correct
finding: RefactoringMiner analysed 248 commits of the Kafka target's drift and
reported 326 refactorings, none of them touching the five files these SAPs are
about. The landing sites did not move.
