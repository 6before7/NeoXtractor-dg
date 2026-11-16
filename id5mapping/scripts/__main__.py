"""Decompile a 2017-2018 Identity V script.npk from the command line.

Usage:
    python -m id5mapping.scripts path/to/script.npk OUT_DIR [--uncompyle2 DIR] [--align-with DEC2017.npk] ...
"""

import argparse
import multiprocessing
import os
import shutil

from core.args import arguments
from core.logger import setup_logger

from .decompile import decompile_archive


def main(argv=None):
    """Parse the command line and run the decompile."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("npk", help="script.npk")
    parser.add_argument("out", help="output folder (must not exist)")
    parser.add_argument("--align-with", nargs="*", default=[], metavar="NPK",
                        help="script.npk of an already-mapped build (Dec 2017 for Feb-May 2018 builds)")
    parser.add_argument("--python2", default=shutil.which("python2.7") or shutil.which("python2"),
                        help="Python 2.7 interpreter, for verification (default: found on PATH)")
    parser.add_argument("--uncompyle2", help="uncompyle2 1.1 source folder (second decompiler)")
    parser.add_argument("--only", nargs="*", default=[], help="only modules whose path contains one of these")
    parser.add_argument("--include-stdlib", action="store_true", help="also decompile the bundled lib/")
    parser.add_argument("--max-size", type=int, default=0, help="skip modules with more bytecode than this")
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    parser.add_argument("--timeout", type=int, default=300, help="seconds per module per decompiler")
    parser.add_argument("--keep-work", action="store_true", help="keep the .pyc files and every candidate output")
    args = parser.parse_args(argv)
    if os.path.exists(args.out):
        parser.error(f"output folder exists: {args.out}")
    decompile_archive(args.npk, args.out, reference_archives=args.align_with, python2=args.python2,
                      uncompyle2=args.uncompyle2, only=args.only, include_stdlib=args.include_stdlib,
                      max_size=args.max_size, jobs=args.jobs, timeout=args.timeout, keep_work=args.keep_work)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    vars(arguments).setdefault("log_level", None)  # NeoXtractor's own arguments are not parsed here
    setup_logger()
    main()
