"""Command line for npkscript.pipeline."""
import argparse
import os
import sys

from . import pipeline

USAGE = """npk_decompile: readable Python from a 2017-2018 Identity V (NeoX) script.npk, statically.

    python -m npkscript script.npk OUT_DIR [options]

decrypt every entry -> recover the opcode permutation -> rewrite to CPython 2.7 numbering (.pyc; fused
instructions expanded) -> decompile each module with several decompilers -> with Python 2.7, keep for every
function the candidate whose recompiled bytecode equals the original -> write the source tree
"""


def main(argv=None):
    d = pipeline.Options()
    ap = argparse.ArgumentParser(description=USAGE, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('npk', help='script.npk (NXPK archive)')
    ap.add_argument('out', help='new output directory')
    ap.add_argument('--only', nargs='*', default=[], help='only modules whose path contains one of these strings')
    ap.add_argument('--include-stdlib', action='store_true', help='also decompile the bundled lib/ standard library')
    ap.add_argument('--align-with', nargs='*', default=[], help='script.npk of related builds to align against '
                    '(resolves opcodes the standard library never uses, and fused instructions)')
    ap.add_argument('--python2', default=d.python2, help='Python 2.7 interpreter (verification and uncompyle2)')
    ap.add_argument('--uncompyle2', help='uncompyle2 1.1 source directory (the folder containing uncompyle2/)')
    ap.add_argument('--extra', nargs='*', default=[], help='other decompiles of the same build to use as candidates')
    ap.add_argument('--jobs', type=int, default=d.jobs)
    ap.add_argument('--timeout', type=int, default=d.timeout, help='seconds per module per decompiler (default 300)')
    ap.add_argument('--max-size', type=int, default=0, help='quick look: skip modules with more bytecode than this')
    ap.add_argument('--keep-work', action='store_true', help='keep intermediate .pyc and candidate outputs')
    a = ap.parse_args(argv)
    opts = pipeline.Options(python2=a.python2, uncompyle2=a.uncompyle2, only=a.only,
                            include_stdlib=a.include_stdlib, align_with=a.align_with, extra=a.extra, jobs=a.jobs,
                            timeout=a.timeout, max_size=a.max_size, keep_work=a.keep_work,
                            log=lambda s: print(s, flush=True))
    try:
        pipeline.decompile_npk(a.npk, a.out, opts)
    except (FileExistsError, RuntimeError) as e:
        sys.exit(str(e))
