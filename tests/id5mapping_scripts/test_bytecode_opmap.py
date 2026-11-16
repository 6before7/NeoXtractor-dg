"""Tests for id5mapping.scripts bytecode reading and opcode recovery (no game data needed).

Run from the repository root: python -m unittest discover -s tests/id5mapping_scripts -t .
"""

import shutil
import subprocess
import unittest

from id5mapping.scripts import bytecode, opmap

PY2 = shutil.which("python2.7") or shutil.which("python2")
SOURCE = "def f(a, b=2):\n    return [x for x in range(a) if x % b]\nclass C(object):\n    def g(self):\n        return 1\n"


def py27_marshal(source: str) -> bytes:
    return subprocess.check_output(
        [PY2, "-E", "-s", "-B", "-c",
         "import marshal,sys;sys.stdout.write(marshal.dumps(compile(sys.stdin.read(),'m.py','exec')))"],
        input=source.encode())


class Opmap(unittest.TestCase):
    def test_expand_fused_packing(self):
        self.assertEqual(opmap.expand_fused(("fused", "LOAD_FAST", "LOAD_CONST", "hi/lo"), 0x0205),
                         [("LOAD_FAST", 2), ("LOAD_CONST", 5)])
        self.assertEqual(opmap.expand_fused(("fused", "LOAD_FAST", "LOAD_CONST", "lo/hi"), 0x0205),
                         [("LOAD_FAST", 5), ("LOAD_CONST", 2)])

    def test_aligned_requires_equal_operands(self):
        ref = bytes((100, 1, 0, 83))                    # LOAD_CONST 1; RETURN_VALUE
        ours = bytes((200, 1, 0, 7))                    # shuffled numbers, same operands
        self.assertEqual(opmap._aligned(ours, ref, opmap.NAME_OF.get),
                         [(200, "LOAD_CONST", 0), (7, "RETURN_VALUE", 0)])
        self.assertIsNone(opmap._aligned(bytes((200, 2, 0, 7)), ref, opmap.NAME_OF.get))

    def test_run_parity_tells_paired_halves_apart(self):
        found = opmap._aligned(bytes((201, 0, 0, 201, 1, 0)), bytes((124, 0, 0, 106, 1, 0)), opmap.NAME_OF.get)
        self.assertEqual([(n, p) for _, n, p in found], [("LOAD_FAST", 0), ("LOAD_ATTR", 1)])


@unittest.skipUnless(PY2, "needs Python 2.7")
class Bytecode(unittest.TestCase):
    def test_reads_code_objects_with_offsets(self):
        data = py27_marshal(SOURCE)
        code = bytecode.load_code(data)
        paths = [p for p, _ in code.walk()]
        self.assertIn("/<module>/f", paths)
        self.assertIn("/<module>/C/g", paths)
        for _, obj in code.walk():
            self.assertEqual(data[obj.code_at:obj.code_at + len(obj.code)], obj.code)
            if obj.lnotab_at is not None:
                self.assertEqual(data[obj.lnotab_at:obj.lnotab_at + len(obj.lnotab)], obj.lnotab)

    def test_load_modules_skips_non_code(self):
        modules = bytecode.load_modules([py27_marshal(SOURCE), b"not code", b""])
        self.assertEqual(list(modules), ["m.py"])


if __name__ == "__main__":
    unittest.main()
