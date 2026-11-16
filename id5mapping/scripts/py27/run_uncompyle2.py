# -*- coding: utf-8 -*-
"""Decompile one .pyc with uncompyle2 1.1 (Python 2.7; uncompyle2 is not bundled, its source folder is passed in).

Usage: python2.7 run_uncompyle2.py <uncompyle2 source dir> <file.pyc> <out.py> [memory limit in bytes]
"""

import sys

if len(sys.argv) > 4:
    try:
        import resource  # POSIX only: fail on a runaway module instead of exhausting memory
        resource.setrlimit(resource.RLIMIT_AS, (int(sys.argv[4]), int(sys.argv[4])))
    except ImportError:
        pass

sys.path.insert(0, sys.argv[1])
import uncompyle2  # noqa: E402

with open(sys.argv[3], 'w') as out:
    uncompyle2.uncompyle_file(sys.argv[2], out)
