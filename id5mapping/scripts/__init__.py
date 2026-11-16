"""Decompile the Python scripts of 2017-2018 Identity V ``script.npk`` archives (NeoX, Python 2.7).

``bytecode`` reads the code objects NeoXtractor's reader decrypts, ``opmap`` recovers the build's opcode numbering
from evidence in the archive, ``rewrite`` turns a module into a CPython 2.7 ``.pyc``. Game code is never imported
or executed.
"""

from .bytecode import CodeObject, ScriptModule, instructions, load_code, load_modules
from .opmap import OpcodeMap, recover_opcode_map
from .rewrite import RewriteError, to_pyc, unmapped_opcodes

__all__ = [
    "CodeObject",
    "OpcodeMap",
    "RewriteError",
    "ScriptModule",
    "instructions",
    "load_code",
    "load_modules",
    "recover_opcode_map",
    "to_pyc",
    "unmapped_opcodes",
]
