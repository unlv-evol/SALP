<!--
Thanks for contributing. Delete any section that does not apply — a short,
accurate PR beats a long template filled in for its own sake.
-->

## Implemented component

<!-- The component or feature completed, e.g. "Verification Evidence",
     "SAP Characterization". Link the issue: Closes #123 -->

## Specification requirements addressed

<!--
Sections of the SAP Design Specification and the Implementation & Onboarding
Guide this implements, so the work is traceable to what required it.
e.g. Design Spec §5 (Evidence Representation); Onboarding Guide §13.
-->

## SAP fields produced or modified

<!--
Which evidence categories, required elements, schema fields, or characterization
fields this adds or changes. Write "none" for tooling or docs.
-->

## Type of change

- [ ] Bug fix
- [ ] New or improved analyzer / evidence recovery
- [ ] Parser change (GACPD record or diff handling)
- [ ] Characterization or scoring change
- [ ] Refactor (no behaviour change)
- [ ] Docs, tooling, or CI

## Characterization impact

<!--
Most changes here move the numbers. Run `make run` against your sample before
and after, and report it. Write "none" for a docs or tooling change.
-->

| | Before | After |
| --- | --- | --- |
| Readiness | | |
| Mean Coverage | | |
| Mean Fidelity | | |

Categories whose evidence states changed, and why:

## Evidence-model checklist

<!-- Only if this PR touches an analyzer or the characterization engine. -->

- [ ] The category is built through `self.draft(...)`, so every required element
      carries an explicit outcome
- [ ] The evidence state is the right one of the four — in particular
      `NOT_APPLICABLE` (leaves both denominators) is not used where
      `VERIFIED_ABSENT` (credited to Coverage) is meant
- [ ] `tool_version()` reports the version actually installed
- [ ] A completed investigation that found nothing returns `VERIFIED_ABSENT`, not
      `UNAVAILABLE` — and an investigation that could not run returns
      `UNAVAILABLE`, not `VERIFIED_ABSENT`
- [ ] Partial recovery is `PRESENT` with a representation below 1.0, not a failure
- [ ] A missing or unconfigured tool returns `UNAVAILABLE` with a diagnostic
      naming the fix; nothing raises
- [ ] `blocking_conflict` is set only where a concrete obstacle is actually
      demonstrated — it caps Readiness at Low regardless of Coverage and Fidelity
- [ ] Output is deterministic: anything sorted uses a total key

## Layering checklist

<!-- Only if this PR adds or moves modules. -->

- [ ] Imports go through a package's public surface (`salp.repos`, not
      `salp.repos.cache`), and new public names are added to its `__all__`
- [ ] Dependencies run one way: `models` → `ingest`/`repos`/`structural` →
      `analyzers` → `characterization`/`packaging` → `pipeline`

## Tests added

<!-- Which suites: unit, integration, schema-validation, evidence-state,
     characterization, regression, end-to-end. -->

- [ ] `make check` passes (ruff, mypy, pytest)
- [ ] New behaviour has a test that fails without the change
- [ ] Tests needing a real GACPD sample live in `tests/integration/` and skip
      themselves when `data/gacpd/` is absent

<!-- If you verified against a real sample, say which repositories and PRs. -->

## Known limitations

<!--
Incomplete functionality, assumptions, constraints, temporary workarounds, or
planned follow-ups a reviewer should know about. Write "none" if there are none.
-->

## AI-assisted development

<!--
If AI-assisted tools (Claude Code, Copilot, Codex, …) were used substantially for
code generation, testing, or debugging, disclose it here and say what for.
Write "none" otherwise.
-->

## Documentation

- [ ] README / CONTRIBUTING updated if the layout, commands, or numbers changed
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] Code adapted from elsewhere is recorded in `docs/adoption/`, including any
      behaviour that was deliberately changed

## Reviewer checklist

- [ ] The implementation satisfies the referenced requirements
- [ ] It conforms to the SAP Design Specification and the Onboarding Guide
- [ ] Evidence objects are correctly constructed, with provenance and diagnostics
- [ ] The generated SAP representations are correct
- [ ] Tests are adequate and pass
- [ ] Documentation is updated
- [ ] No unresolved issues prevent merging
