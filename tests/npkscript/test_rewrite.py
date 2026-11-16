"""Unit tests for npkscript opcode rewriting (no game data needed). Run: python -m unittest discover -s tests/npkscript"""
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.dont_write_bytecode = True
from npkscript import marshal27, rewrite  # noqa: E402
from npkscript.opcodes import CPY27, Fused, Paired, instructions  # noqa: E402

PY2 = shutil.which('python2.7') or shutil.which('python2')


def asm(*ins):
    b = bytearray()
    for name, arg in ins:
        op = CPY27[name] if isinstance(name, str) else name
        b += bytes((op, arg & 0xff, arg >> 8)) if op >= 90 else bytes((op,))
    return bytes(b)


IDENTITY = {v: k for k, v in CPY27.items()}


class Relocation(unittest.TestCase):
    def test_fused_expansion_retargets_jumps(self):
        F = 200                                         # a fused opcode: LOAD_FAST hi, LOAD_CONST lo
        mapping = {**IDENTITY, F: Fused('LOAD_FAST', 'LOAD_CONST', 'hi/lo')}
        # 0 SETUP_LOOP ->12 ; 3 F(0,1) ; 6 POP_JUMP_IF_FALSE ->11 ; 9 POP_TOP? (1 byte) ...
        code = asm(('SETUP_LOOP', 9), (F, 0x0001), ('POP_JUMP_IF_FALSE', 11), ('POP_TOP', None),
                   ('POP_TOP', None), ('POP_BLOCK', None), ('LOAD_CONST', 0), ('RETURN_VALUE', None))
        entries = rewrite._names(code, mapping)
        ins, new_off = rewrite._relocate(entries)
        self.assertEqual(ins[:3], [('SETUP_LOOP', 12), ('LOAD_FAST', 0), ('LOAD_CONST', 1)])
        self.assertEqual(ins[3], ('POP_JUMP_IF_FALSE', 14))   # old target 11 moved by 3
        self.assertEqual(new_off[11], 14)

    def test_jump_into_middle_is_refused(self):
        code = asm(('JUMP_ABSOLUTE', 1), ('RETURN_VALUE', None))
        with self.assertRaises(rewrite.Unsupported):
            rewrite._relocate(rewrite._names(code, {**IDENTITY, 200: Fused('LOAD_FAST', 'LOAD_CONST', 'hi/lo')}))

    def test_paired_by_run_position(self):
        P = 201
        mapping = {**IDENTITY, P: Paired('LOAD_FAST', 'LOAD_ATTR')}
        names = [p[0] for _, _, parts in rewrite._names(asm((P, 0), (P, 1), (P, 0), (P, 2)), mapping) for p in parts]
        self.assertEqual(names, ['LOAD_FAST', 'LOAD_ATTR', 'LOAD_FAST', 'LOAD_ATTR'])

    def test_lnotab_remap(self):
        # line +1 at offset 3 and +2 at offset 9; offsets 3 -> 6 and 9 -> 12 after a 3-byte expansion at 0
        lt = rewrite._lnotab(bytes((3, 1, 6, 2)), {0: 0, 3: 6, 6: 9, 9: 12, 12: 15})
        self.assertEqual(lt, bytes((6, 1, 6, 2)))

    def test_dead_jump_after_backward_jump(self):
        ins = [('LOAD_CONST', 0), ('JUMP_ABSOLUTE', 0), ('JUMP_FORWARD', 0), ('RETURN_VALUE', None)]
        self.assertEqual(rewrite._dead_jumps(ins)[2:5], [('NOP', None)] * 3)


@unittest.skipUnless(PY2, 'needs Python 2.7')
class RoundTrip(unittest.TestCase):
    """Compile a module with Python 2.7, shuffle its opcode numbers as NeoX does, and check that rewriting with the
    inverse map restores the original bytes exactly."""

    SRC = 'def f(a, b=2):\n    for i in range(a):\n        if i % b:\n            continue\n        a += i\n    return a\n' \
          'class C(object):\n    def g(self):\n        return {k: v for k, v in self.__dict__.items()}\n'

    def test_permutation_round_trip(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, 'm.py')
            open(src, 'w').write(self.SRC)
            out = os.path.join(tmp, 'm.bin')
            subprocess.check_call([PY2, '-E', '-s', '-B', '-c', 'import marshal,sys;'
                                   'open(sys.argv[2],"wb").write(marshal.dumps(compile(open(sys.argv[1]).read(),'
                                   '"m.py","exec")))', src, out])
            data = open(out, 'rb').read()
            mod = marshal27.Module(0, data)
            low = [o for o in CPY27.values() if o < 90]; high = [o for o in CPY27.values() if o >= 90]
            rng = random.Random(7)
            perm = dict(zip(low, rng.sample(low, len(low)))); perm.update(zip(high, rng.sample(high, len(high))))
            shuffled = bytearray(data)
            for _, c in mod.code.walk():
                for off, op, _ in instructions(c.code):
                    shuffled[c.code_at + off] = perm[op]
            smod = marshal27.Module(0, bytes(shuffled))
            mapping = {perm[o]: n for n, o in CPY27.items()}
            self.assertEqual(rewrite.to_pyc(smod, mapping)[8:], data)
        finally:
            shutil.rmtree(tmp)


if __name__ == '__main__':
    unittest.main()
