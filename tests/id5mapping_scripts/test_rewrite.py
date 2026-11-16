"""Tests for id5mapping.scripts.rewrite (no game data needed).

Run from the repository root: python -m unittest discover -s tests/id5mapping_scripts -t .
"""

import random
import shutil
import subprocess
import unittest

from id5mapping.scripts import bytecode, rewrite
from id5mapping.scripts.bytecode import CPY27

PY2 = shutil.which("python2.7") or shutil.which("python2")
IDENTITY = {number: name for name, number in CPY27.items()}
FUSED = ("fused", "LOAD_FAST", "LOAD_CONST", "hi/lo")


def assemble(*ins):
    code = bytearray()
    for name, arg in ins:
        op = CPY27[name] if isinstance(name, str) else name
        code += bytes((op, arg & 0xFF, arg >> 8)) if op >= 90 else bytes((op,))
    return bytes(code)


class Relocation(unittest.TestCase):
    def test_fused_expansion_retargets_jumps(self):
        code = assemble(("SETUP_LOOP", 9), (200, 0x0001), ("POP_JUMP_IF_FALSE", 11), ("POP_TOP", None),
                        ("POP_TOP", None), ("POP_BLOCK", None), ("LOAD_CONST", 0), ("RETURN_VALUE", None))
        ins, new_offset = rewrite._relocate(rewrite._translate(code, {**IDENTITY, 200: FUSED}))
        self.assertEqual(ins[:4], [("SETUP_LOOP", 12), ("LOAD_FAST", 0), ("LOAD_CONST", 1), ("POP_JUMP_IF_FALSE", 14)])
        self.assertEqual(new_offset[11], 14)

    def test_jump_into_an_instruction_is_refused(self):
        code = assemble(("JUMP_ABSOLUTE", 1), (200, 0), ("RETURN_VALUE", None))
        with self.assertRaises(rewrite.RewriteError):
            rewrite._relocate(rewrite._translate(code, {**IDENTITY, 200: FUSED}))

    def test_paired_by_position_in_run(self):
        mapping = {**IDENTITY, 201: ("pair", "LOAD_FAST", "LOAD_ATTR")}
        names = [p[0] for _, _, parts in rewrite._translate(assemble((201, 0), (201, 1), (201, 0), (201, 2)), mapping)
                 for p in parts]
        self.assertEqual(names, ["LOAD_FAST", "LOAD_ATTR", "LOAD_FAST", "LOAD_ATTR"])

    def test_line_table_follows_relocation(self):
        self.assertEqual(rewrite._remap_lnotab(bytes((3, 1, 6, 2)), {0: 0, 3: 6, 6: 9, 9: 12, 12: 15}),
                         bytes((6, 1, 6, 2)))

    def test_dead_jump_after_backward_jump(self):
        ins = [("LOAD_CONST", 0), ("JUMP_ABSOLUTE", 0), ("JUMP_FORWARD", 0), ("RETURN_VALUE", None)]
        self.assertEqual(rewrite._blank_dead_jumps(ins)[2:5], [("NOP", None)] * 3)


@unittest.skipUnless(PY2, "needs Python 2.7")
class RoundTrip(unittest.TestCase):
    """Shuffle a real Python 2.7 module's opcode numbers as NeoX does; rewriting restores it byte for byte."""

    SOURCE = ("def f(a, b=2):\n    for i in range(a):\n        if i % b:\n            continue\n        a += i\n"
              "    return a\nclass C(object):\n    def g(self):\n        return {k: v for k, v in self.__dict__.items()}\n")

    def test_permutation_round_trip(self):
        data = subprocess.check_output(
            [PY2, "-E", "-s", "-B", "-c",
             "import marshal,sys;sys.stdout.write(marshal.dumps(compile(sys.stdin.read(),'m.py','exec')))"],
            input=self.SOURCE.encode())
        module = bytecode.ScriptModule("m.py", bytecode.load_code(data), data)
        low = [o for o in CPY27.values() if o < 90]
        high = [o for o in CPY27.values() if o >= 90]
        rng = random.Random(7)
        perm = dict(zip(low, rng.sample(low, len(low))))
        perm.update(zip(high, rng.sample(high, len(high))))
        shuffled = bytearray(data)
        for _, code in module.code.walk():
            for offset, op, _ in bytecode.instructions(code.code):
                shuffled[code.code_at + offset] = perm[op]
        shuffled_module = bytecode.ScriptModule("m.py", bytecode.load_code(bytes(shuffled)), bytes(shuffled))
        mapping = {perm[number]: name for name, number in CPY27.items()}
        self.assertEqual(rewrite.to_pyc(shuffled_module, mapping)[8:], data)


if __name__ == "__main__":
    unittest.main()
