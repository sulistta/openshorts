"""Command entry point: stdio MCP by default, explicit legacy migration only."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .store import StoreError, migrate_legacy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="openshorts-mcp",
        description="OpenShorts local stdio MCP server",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--output-dir",
        help="Override OPENSHORTS_OUTPUT_DIR for this invocation.",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser(
        "migrate-legacy",
        help="Rename a legacy output directory to output-legacy-<timestamp> and start clean.",
    )
    args = parser.parse_args(argv)

    if args.command == "migrate-legacy":
        try:
            result = migrate_legacy(args.output_dir)
        except StoreError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command is not None:
        parser.error(f"Unsupported command: {args.command}")
    try:
        from .server import run_stdio

        run_stdio(args.output_dir)
    except StoreError as exc:
        print(f"OpenShorts MCP could not start: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
