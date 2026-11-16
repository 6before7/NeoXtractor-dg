# Python 2.7 helper: decompile one .pyc with uncompyle2 1.1 (pure Python; not bundled, see README).
# args: <uncompyle2 source dir> <pyc> <out .py>. Nothing from the game is executed.
import sys
sys.path.insert(0, sys.argv[1])
import uncompyle2
with open(sys.argv[3], 'w') as out:
    uncompyle2.uncompyle_file(sys.argv[2], out)
