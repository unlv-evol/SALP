"""Command-line interface for SALP."""

from __future__ import annotations

import argparse
from pathlib import Path

from salp import __version__
from salp.config import Config, configure, get_logger

log = get_logger("salp.cli")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="salp", description="Semantic Alignment Pipeline")
    p.add_argument("--version", action="version", version=f"salp {__version__}")
    p.add_argument("-c", "--config", type=Path, default=None, help="path to config YAML")
    p.add_argument("--log-level", default=None, help="override log level")

    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="ingest GACPD output and construct SAPs")
    run.add_argument("--gacpd-run", type=Path, default=None, help="GACPD run directory")
    run.add_argument("--output", type=Path, default=None, help="output directory")
    run.add_argument("--repo-cache", type=Path, default=None, help="local bare-clone cache")
    run.add_argument(
        "--no-resolve-pins", action="store_true",
        help="skip repository-state pin resolution (leaves every pin date-based)",
    )

    fetch = sub.add_parser(
        "fetch-repos",
        help="clone the source and target repositories a run needs (the only network step)",
    )
    fetch.add_argument("--gacpd-run", type=Path, default=None, help="GACPD run directory")
    fetch.add_argument("--repo-cache", type=Path, default=None, help="local bare-clone cache")
    fetch.add_argument(
        "--dry-run", action="store_true", help="report what would be fetched, and stop"
    )

    sub.add_parser("categories", help="list registered evidence categories")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = Config.load(args.config)
    if args.log_level:
        config.log_level = args.log_level
    configure(config.log_level)

    if args.command in ("run", "fetch-repos"):
        if args.gacpd_run:
            config.paths.gacpd_run = args.gacpd_run
        if args.repo_cache:
            config.paths.repo_cache = args.repo_cache

    if args.command == "run":
        if args.output:
            config.paths.output = args.output
        if args.no_resolve_pins:
            config.resolve_pins = False
        from salp.pipeline import run as run_pipeline

        minted = run_pipeline(config)
        print(f"Minted {minted} SAP(s) to {config.paths.output}")
        return 0

    if args.command == "fetch-repos":
        from salp.pipeline import fetch_repositories

        failures = fetch_repositories(config, dry_run=args.dry_run)
        if failures:
            print(f"{failures} repository operation(s) failed; see the log")
            return 1
        print(f"Repository cache ready at {config.paths.repo_cache}")
        return 0

    if args.command == "categories":
        from salp.analyzers import build_all

        for a in build_all():
            print(f"{a.category.value:24s} <- {a.component_name} ({a.tool or 'builtin'})")
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
