"""SALP orchestrator.

Ties the stages together for a GACPD run:

    discover PRs -> for each MO file: build SAP -> validate -> characterize
    -> serialize (sap dir) -> write PR manifest + _context payloads.

Only MO files are minted as SAPs; NA/ED files are retained as context payloads
under the pull request and referenced by the MO SAPs of that pull request.
"""

from __future__ import annotations

import re
from pathlib import Path

from salp.analyzers.tools import run_refactoring_miner
from salp.characterization import CharacterizationProfile, Characterizer, aggregate_readiness
from salp.config import Config, get_logger
from salp.ingest import GACPDPullRequest, discover_pull_requests
from salp.models import SAP, Category, CategoryEvidence, ContextFile, PRGroup, SAPReference
from salp.packaging import build_sap, validate_sap, write_pr_group, write_sap
from salp.repos import (
    PinResolver,
    clone,
    fetch_default,
    fetch_pull_request,
    has_pull_request_ref,
    is_cloned,
    is_slug,
    repo_dir,
)

log = get_logger(__name__)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:80]


def _pr_number(pr_id: str) -> str:
    m = re.search(r"(\d+)", pr_id)
    return m.group(1) if m else pr_id


def _camel(text: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", text) if p)


def _repo_token(repo: str) -> str:
    """``linkedin/kafka`` -> ``linkedinKafka``."""
    owner, _, name = repo.partition("/")
    prefix = _camel(owner)
    return f"{prefix[:1].lower()}{prefix[1:]}{_camel(name)}"


def variant_pair_dirname(
    target_repo: str | None, source_repo: str | None, fallback: str
) -> str:
    """The output directory grouping a variant pair's pull requests.

    Named target-first (``linkedinKafka-apacheKafka``) so the divergent variant
    the changes are being ported *into* leads, matching how runs are identified.
    Falls back to the GACPD run directory when the pair could not be recovered,
    so output is always grouped even for an unparsed record.
    """
    if not (target_repo and source_repo):
        return fallback
    return f"{_repo_token(target_repo)}-{_repo_token(source_repo)}"


def characterize_sap(sap: SAP) -> dict[str, CharacterizationProfile]:
    """Characterize every hunk of a SAP; returns {hunk_id: profile}."""
    ch = Characterizer()
    profiles: dict[str, CharacterizationProfile] = {}
    for hunk in sap.hunks:
        cats: dict[Category, CategoryEvidence] = {
            Category(name): ce for name, ce in hunk.categories.items()
        }
        profiles[hunk.hunk_id] = ch.characterize(
            cats,
            sap.change_type,
            localization_ambiguous=hunk.localization_ambiguous,
            edit_region_unassociated=hunk.edit_region_unassociated,
        )
    return profiles


def _context_files(pr: GACPDPullRequest) -> tuple[list[ContextFile], dict[str, str]]:
    """Retain NA/ED siblings as PR-level context payloads.

    A context file is only referenced when GACPD actually retained a copyable
    payload; otherwise the manifest records the classification and a diagnostic
    rather than a dangling ``_context/`` path.
    """
    entries: list[ContextFile] = []
    payloads: dict[str, str] = {}
    for cf in pr.context_files:
        entry = ContextFile(
            gacpd_classification=cf.classification,
            source_file=cf.display_name,
        )
        if cf.context_payload is not None:
            try:
                payloads[f"_context/{cf.display_name}"] = cf.context_payload.read_text(
                    encoding="utf-8", errors="replace"
                )
                entry.path = f"_context/{cf.display_name}"
            except OSError as exc:
                entry.diagnostics = f"payload unreadable: {exc}"
        else:
            entry.diagnostics = "GACPD retained no whole-file payload for this sibling"
        entries.append(entry)
    return entries, payloads


def fetch_repositories(config: Config, *, dry_run: bool = False) -> int:
    """Populate the local clone cache for everything a run will need.

    This is the pipeline's only network operation, kept out of ``run`` so that
    construction stays deterministic and offline. It clones each repository the
    GACPD output names, then fetches the head of every pull request so the
    source side can be pinned to a commit.

    Returns the number of repositories that could not be prepared.
    """
    cache_dir = config.paths.repo_cache
    prs = discover_pull_requests(config.paths.gacpd_run)

    repos: set[str] = set()
    pr_heads: set[tuple[str, str]] = set()
    for pr in prs:
        for repo in (pr.metadata.source_repo, pr.metadata.target_repo):
            if is_slug(repo):
                repos.add(str(repo))
        if is_slug(pr.metadata.source_repo) and pr.metadata.number:
            pr_heads.add((str(pr.metadata.source_repo), pr.metadata.number))

    if not repos:
        log.warning("no repositories named by the GACPD output under %s", config.paths.gacpd_run)
        return 0

    log.info(
        "%d repositor%s needed; %d pull-request head(s) to fetch",
        len(repos), "y" if len(repos) == 1 else "ies", len(pr_heads),
    )
    if dry_run:
        for repo in sorted(repos):
            state = "present" if is_cloned(cache_dir, repo) else "missing"
            log.info("  %-40s %s -> %s", repo, state, repo_dir(cache_dir, repo))
        return 0

    failures = 0
    for repo in sorted(repos):
        outcome = clone(cache_dir, repo)
        if not outcome.ok:
            log.error("could not clone %s: %s", repo, outcome.error)
            failures += 1
        elif outcome.cloned:
            log.info("cloned %s", repo)
        else:
            log.info("%s already present; refreshing", repo)
            fetch_default(cache_dir, repo)

    for repo, number in sorted(pr_heads):
        if not is_cloned(cache_dir, repo):
            continue
        if has_pull_request_ref(cache_dir, repo, number):
            continue
        result = fetch_pull_request(cache_dir, repo, number)
        if not result.ok:
            log.warning("could not fetch %s PR #%s: %s", repo, number, result.stderr.strip())
            failures += 1

    return failures


def run(config: Config) -> int:
    """Execute the pipeline. Returns the number of SAPs minted."""
    run_dir = config.paths.gacpd_run
    out_dir = config.paths.output
    resolver = PinResolver(config.paths.repo_cache, enabled=config.resolve_pins)
    prs = discover_pull_requests(run_dir)
    log.info("discovered %d pull request(s) under %s", len(prs), run_dir)

    minted = 0
    for pr in prs:
        number = pr.metadata.number or _pr_number(pr.pr_id)
        pr_key = f"PR-{number}"
        pair = variant_pair_dirname(
            pr.metadata.target_repo, pr.metadata.source_repo, pr.pr_dir.parent.name
        )
        pr_dir = out_dir / pair / pr_key
        change_id = f"RC-{number}"
        group = PRGroup(
            pr_id=pr_key,
            variant_pair=pair,
            source_repo=pr.metadata.source_repo,
            target_repo=pr.metadata.target_repo,
            pull_request=pr.metadata.as_manifest_block(),
        )
        group.context_files, group.payloads = _context_files(pr)

        # Pins bind the SAP to exact repository states; resolution is local,
        # against clones populated separately by `salp fetch-repos`.
        source_pin = resolver.source(pr.metadata.source_repo, pr.metadata.number)
        target_pin = resolver.target(
            pr.metadata.target_repo, pr.metadata.cutoff_timestamp or pr.metadata.cutoff_date
        )
        # Refactorings are detected once per pull request, over the target's
        # drift between divergence and cutoff -- the divergence the adaptation
        # has to reconcile -- and then filtered per file by the analyzer.
        divergence_pin = resolver.target(
            pr.metadata.target_repo,
            pr.metadata.divergence_timestamp or pr.metadata.divergence_date,
        )
        refactorings: tuple[dict[str, object], ...] | str
        if not config.detect_refactorings:
            refactorings = "refactoring detection disabled (detect_refactorings=false)"
        else:
            refactorings = run_refactoring_miner(
                config.tools.get_refactoringminer_jar(),
                repo_dir(config.paths.repo_cache, pr.metadata.target_repo or ""),
                divergence_pin.commit if divergence_pin else None,
                target_pin.commit if target_pin else None,
                config.paths.repo_cache / ".refactoring-cache",
                config.tools.refactoringminer_timeout,
            )

        for gf in pr.mo_files:
            stem = Path(gf.display_name).stem or gf.name
            sap_id = f"{change_id}-{_slug(stem)}"
            sap = build_sap(
                gf,
                sap_id=sap_id,
                change_id=change_id,
                pr=pr,
                source_pin=source_pin,
                target_pin=target_pin,
                cache_dir=config.paths.repo_cache,
                refactorings=refactorings,
                refactoringminer_jar=config.tools.get_refactoringminer_jar(),
            )

            errors = validate_sap(sap)
            if errors:
                log.warning("SAP %s has validation issues: %s", sap_id, "; ".join(errors))

            profiles = characterize_sap(sap)
            sap_path = f"sap-{_slug(stem)}"
            write_sap(sap, pr_dir / sap_path, profiles)

            group.saps.append(SAPReference(
                sap_id=sap_id,
                gacpd_classification="MO",
                path=f"{sap_path}/",
                source_file=gf.display_name,
                target_file=sap.target_file,
                hunk_count=len(sap.hunks),
                readiness_ref=f"{sap_path}/characterization.json#aggregate",
            ))
            minted += 1
            final = aggregate_readiness(list(profiles.values()))
            log.info("minted %s (%d hunk(s), readiness=%s)", sap_id, len(sap.hunks), final.name)

        if group.saps:
            write_pr_group(group, pr_dir)

    log.info("minted %d SAP(s) total", minted)
    return minted
