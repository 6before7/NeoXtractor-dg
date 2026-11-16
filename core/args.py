"""Argument management module."""

import argparse

from core.build_info import BuildInfo

parser = argparse.ArgumentParser(description="NeoXtractor")
parser.add_argument(
    "--version",
    "-v",
    action="version",
    version=f"{BuildInfo.version if BuildInfo.version else 'development'} (Build: {
        BuildInfo.commit_hash[:7] if BuildInfo.commit_hash else 'unknown'
    })",
)
parser.add_argument(
    "--log-level",
    help="Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL or integer values)",
    dest="log_level",
)

_subparsers = parser.add_subparsers(help="subcommand help", dest="subcommand")

gui_parser = _subparsers.add_parser("gui", help="Run the NeoXtractor GUI")

decompile_parser = _subparsers.add_parser(
    "decompile",
    help="Decompile a 2017-2018 Identity V script.npk (npkscript; see 'decompile --help')",
    add_help=False,
)

arguments = argparse.Namespace()


def parse_args():
    """Parse arguments. Everything after `decompile` is passed on to npkscript's own parser."""
    _, rest = parser.parse_known_args(namespace=arguments)
    if arguments.subcommand == "decompile":
        arguments.decompile_args = rest
    elif rest:
        parser.error(f"unrecognized arguments: {' '.join(rest)}")
