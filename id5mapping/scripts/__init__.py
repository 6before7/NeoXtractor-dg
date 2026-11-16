"""Decompile the Python scripts of 2017-2018 Identity V ``script.npk`` archives (NeoX, Python 2.7).

``bytecode`` reads the code objects NeoXtractor's reader decrypts, ``opmap`` recovers the build's opcode numbering
from evidence in the archive. Game code is never imported or executed.
"""

from .bytecode import CodeObject, ScriptModule, instructions, load_code, load_modules
from .opmap import OpcodeMap, recover_opcode_map

__all__ = [
    "CodeObject",
    "OpcodeMap",
    "ScriptModule",
    "instructions",
    "load_code",
    "load_modules",
    "recover_opcode_map",
]
