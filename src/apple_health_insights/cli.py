"""Command line entry for a local ingest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apple_health_insights.ingest import ingest
from apple_health_insights.parse import ParseError


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "ingest":
        return _ingest(args)
    parser.error(f"Unknown command {args.command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apple-health-insights",
        description=(
            "Turn an Apple Health export into a private aggregate overview and one insight. "
            "Does not contact Google Drive."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    ingest_parser = sub.add_parser(
        "ingest",
        help="Parse a zip, an export.xml file, or a folder that may contain one",
    )
    ingest_parser.add_argument("path", type=Path)
    ingest_parser.add_argument(
        "--out",
        type=Path,
        default=Path("state/health"),
        help="Private directory for the overview and normalized store (default: state/health)",
    )
    ingest_parser.add_argument(
        "--force",
        action="store_true",
        help="Write again even if this file hash was already ingested",
    )
    ingest_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write the overview without printing it",
    )
    return parser


def _ingest(args: argparse.Namespace) -> int:
    try:
        result = ingest(args.path, args.out, force=args.force)
    except ParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"status: {result.status}")
    print(result.message)
    if result.overview_path is not None:
        print(f"overview: {result.overview_path}")
        if not args.quiet and result.overview_path.exists():
            print()
            sys.stdout.write(result.overview_path.read_text(encoding="utf-8"))
    return result.exit_code
