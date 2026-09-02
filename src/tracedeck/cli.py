"""TraceDeck command-line entry point."""

from __future__ import annotations

import argparse
import json

from . import __version__
from .config import ConfigurationError, load_config
from .hook_entry import capture
from .hooks import install, status, uninstall


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracedeck",
        description="Run the local TraceDeck observability service.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")
    hooks = subparsers.add_parser("hooks", help="manage Codex lifecycle hooks")
    hook_commands = hooks.add_subparsers(dest="hooks_command", required=True)
    for name in ("install", "uninstall"):
        command = hook_commands.add_parser(name)
        command.add_argument("--dry-run", action="store_true")
    hook_commands.add_parser("status")
    event = subparsers.add_parser("hook-event", help=argparse.SUPPRESS)
    event.add_argument("event")
    event.add_argument("--owned", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        from .web.app import create_app
        import uvicorn
        config = load_config()
        uvicorn.run(create_app(config), host=config.host, port=config.port, log_level=config.log_level.lower())
        return 0
    if args.command == "hook-event":
        return capture(args.event)
    if args.command == "hooks":
        if args.hooks_command == "install":
            print(install(dry_run=args.dry_run))
        elif args.hooks_command == "uninstall":
            print(uninstall(dry_run=args.dry_run))
        else:
            print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    try:
        config = load_config()
    except ConfigurationError as exc:
        parser.error(str(exc))
    # The service is intentionally not started in P2.1. Later packets provide it.
    print(f"TraceDeck {__version__} configured for {config.host}:{config.port}")
    return 0
