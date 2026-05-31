from __future__ import annotations

import argparse

from agent_treport import CLI_NAME, __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=CLI_NAME)
    parser.add_argument(
        "--version",
        action="version",
        version=f"{CLI_NAME} {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
