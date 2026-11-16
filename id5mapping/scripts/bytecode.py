"""Python 2.7 code objects of an Identity V ``script.npk`` (2017-2018 NeoX builds).

Each script entry, once NeoXtractor has decrypted it (rotor + zlib, see ``core/npk/decompression.py``), is a plain
Python 2.7 ``marshal`` stream of a module code object. NetEase only renumbered the opcodes; everything else is the
standard format. This module reads such a stream and records where each code object's bytecode and line table sit,
so ``rewrite.py`` can patch opcodes in place. Nothing read here is imported or executed.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from typing import Iterable, Iterator

# CPython 2.7 opcode numbers (``opcode.opmap`` of 2.7.18). NeoX keeps the 2.7 rule that opcodes >= 90 take a
# 2-byte argument.
CPY27 = {
    "STOP_CODE": 0, "POP_TOP": 1, "ROT_TWO": 2, "ROT_THREE": 3, "DUP_TOP": 4, "ROT_FOUR": 5, "NOP": 9,
    "UNARY_POSITIVE": 10, "UNARY_NEGATIVE": 11, "UNARY_NOT": 12, "UNARY_CONVERT": 13, "UNARY_INVERT": 15,
    "BINARY_POWER": 19, "BINARY_MULTIPLY": 20, "BINARY_DIVIDE": 21, "BINARY_MODULO": 22, "BINARY_ADD": 23,
    "BINARY_SUBTRACT": 24, "BINARY_SUBSCR": 25, "BINARY_FLOOR_DIVIDE": 26, "BINARY_TRUE_DIVIDE": 27,
    "INPLACE_FLOOR_DIVIDE": 28, "INPLACE_TRUE_DIVIDE": 29, "SLICE+0": 30, "SLICE+1": 31, "SLICE+2": 32,
    "SLICE+3": 33, "STORE_SLICE+0": 40, "STORE_SLICE+1": 41, "STORE_SLICE+2": 42, "STORE_SLICE+3": 43,
    "DELETE_SLICE+0": 50, "DELETE_SLICE+1": 51, "DELETE_SLICE+2": 52, "DELETE_SLICE+3": 53, "STORE_MAP": 54,
    "INPLACE_ADD": 55, "INPLACE_SUBTRACT": 56, "INPLACE_MULTIPLY": 57, "INPLACE_DIVIDE": 58, "INPLACE_MODULO": 59,
    "STORE_SUBSCR": 60, "DELETE_SUBSCR": 61, "BINARY_LSHIFT": 62, "BINARY_RSHIFT": 63, "BINARY_AND": 64,
    "BINARY_XOR": 65, "BINARY_OR": 66, "INPLACE_POWER": 67, "GET_ITER": 68, "PRINT_EXPR": 70, "PRINT_ITEM": 71,
    "PRINT_NEWLINE": 72, "PRINT_ITEM_TO": 73, "PRINT_NEWLINE_TO": 74, "INPLACE_LSHIFT": 75, "INPLACE_RSHIFT": 76,
    "INPLACE_AND": 77, "INPLACE_XOR": 78, "INPLACE_OR": 79, "BREAK_LOOP": 80, "WITH_CLEANUP": 81,
    "LOAD_LOCALS": 82, "RETURN_VALUE": 83, "IMPORT_STAR": 84, "EXEC_STMT": 85, "YIELD_VALUE": 86,
    "POP_BLOCK": 87, "END_FINALLY": 88, "BUILD_CLASS": 89, "STORE_NAME": 90, "DELETE_NAME": 91,
    "UNPACK_SEQUENCE": 92, "FOR_ITER": 93, "LIST_APPEND": 94, "STORE_ATTR": 95, "DELETE_ATTR": 96,
    "STORE_GLOBAL": 97, "DELETE_GLOBAL": 98, "DUP_TOPX": 99, "LOAD_CONST": 100, "LOAD_NAME": 101,
    "BUILD_TUPLE": 102, "BUILD_LIST": 103, "BUILD_SET": 104, "BUILD_MAP": 105, "LOAD_ATTR": 106,
    "COMPARE_OP": 107, "IMPORT_NAME": 108, "IMPORT_FROM": 109, "JUMP_FORWARD": 110, "JUMP_IF_FALSE_OR_POP": 111,
    "JUMP_IF_TRUE_OR_POP": 112, "JUMP_ABSOLUTE": 113, "POP_JUMP_IF_FALSE": 114, "POP_JUMP_IF_TRUE": 115,
    "LOAD_GLOBAL": 116, "CONTINUE_LOOP": 119, "SETUP_LOOP": 120, "SETUP_EXCEPT": 121, "SETUP_FINALLY": 122,
    "LOAD_FAST": 124, "STORE_FAST": 125, "DELETE_FAST": 126, "RAISE_VARARGS": 130, "CALL_FUNCTION": 131,
    "MAKE_FUNCTION": 132, "BUILD_SLICE": 133, "MAKE_CLOSURE": 134, "LOAD_CLOSURE": 135, "LOAD_DEREF": 136,
    "STORE_DEREF": 137, "CALL_FUNCTION_VAR": 140, "CALL_FUNCTION_KW": 141, "CALL_FUNCTION_VAR_KW": 142,
    "SETUP_WITH": 143, "EXTENDED_ARG": 145, "SET_ADD": 146, "MAP_ADD": 147,
}
HAVE_ARGUMENT = 90
# Relative jumps count from the next instruction, absolute ones from the start of the code.
JUMP_RELATIVE = {"FOR_ITER", "JUMP_FORWARD", "SETUP_LOOP", "SETUP_EXCEPT", "SETUP_FINALLY", "SETUP_WITH"}
JUMP_ABSOLUTE = {"JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP", "JUMP_ABSOLUTE", "POP_JUMP_IF_FALSE",
                 "POP_JUMP_IF_TRUE", "CONTINUE_LOOP"}


@dataclass(slots=True)
class CodeObject:
    """The fields of a code object this package uses, plus where its bytecode and line table sit in the stream."""

    name: str
    filename: str
    argcount: int
    flags: int
    code: bytes
    code_at: int
    consts: tuple
    names: list[str]
    varnames: list[str]
    lnotab: bytes = b""
    lnotab_at: int | None = None

    def walk(self, path: str = "") -> Iterator[tuple[str, CodeObject]]:
        """(path, code object) for this code object and every nested one, e.g. ``/<module>/Class/method``."""
        path = f"{path}/{self.name}"
        yield path, self
        for const in self.consts:
            if isinstance(const, CodeObject):
                yield from const.walk(path)


@dataclass(slots=True)
class ScriptModule:
    """One decrypted script entry: its module path, parsed code and the raw marshal bytes."""

    path: str
    code: CodeObject
    data: bytes
    sha256: str = field(init=False)

    def __post_init__(self):
        self.sha256 = hashlib.sha256(self.data).hexdigest()


def instructions(code: bytes) -> Iterator[tuple[int, int, int | None]]:
    """(offset, opcode, argument or None) for each instruction."""
    i = 0
    while i < len(code):
        op = code[i]
        if op >= HAVE_ARGUMENT:
            yield i, op, code[i + 1] | code[i + 2] << 8
            i += 3
        else:
            yield i, op, None
            i += 1


_END = object()  # the marshal dict terminator
_FIXED = {"i": ("<i", 4), "I": ("<q", 8), "g": ("<d", 8)}  # fixed-size scalars
_CONSTANTS = {"0": _END, "N": None, "F": False, "T": True, "S": StopIteration, ".": Ellipsis}


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def load_code(data: bytes) -> CodeObject:
    """Parse a Python 2.7 marshal stream whose top object is a code object."""
    refs: list = []

    def u32(pos: int) -> int:
        return struct.unpack_from("<i", data, pos)[0]

    def load(pos: int):  # returns (value, next position)
        tag = chr(data[pos])
        pos += 1
        if tag in _CONSTANTS:
            return _CONSTANTS[tag], pos
        if tag in _FIXED:
            fmt, size = _FIXED[tag]
            return struct.unpack_from(fmt, data, pos)[0], pos + size
        if tag == "y":
            return complex(*struct.unpack_from("<dd", data, pos)), pos + 16
        if tag in "fx":
            parts = []
            for _ in range(1 if tag == "f" else 2):
                n = data[pos]
                parts.append(float(data[pos + 1:pos + 1 + n].decode("ascii")))
                pos += 1 + n
            return (parts[0] if tag == "f" else complex(*parts)), pos
        if tag == "l":
            n = u32(pos)
            pos += 4
            value = 0
            for j in range(abs(n)):
                value += struct.unpack_from("<h", data, pos + 2 * j)[0] << (15 * j)
            return (-value if n < 0 else value), pos + 2 * abs(n)
        if tag in "stu":
            n = u32(pos)
            raw = data[pos + 4:pos + 4 + n]
            if tag == "t":
                refs.append(raw)
            return (raw.decode("utf-8", "replace") if tag == "u" else raw), pos + 4 + n
        if tag == "R":
            return refs[u32(pos)], pos + 4
        if tag in "([<>":
            n = u32(pos)
            pos += 4
            items = []
            for _ in range(n):
                item, pos = load(pos)
                items.append(item)
            if tag == "(":
                return tuple(items), pos
            return (items if tag == "[" else frozenset(items)), pos
        if tag == "{":
            result = {}
            while True:
                key, pos = load(pos)
                if key is _END:
                    return result, pos
                result[key], pos = load(pos)
        if tag == "c":
            argcount, _nlocals, _stacksize, flags = struct.unpack_from("<4i", data, pos)
            pos += 16
            if chr(data[pos]) != "s":
                raise ValueError("co_code is not a plain string")
            code_at = pos + 5
            code, pos = load(pos)
            consts, pos = load(pos)
            names, pos = load(pos)
            varnames, pos = load(pos)
            _freevars, pos = load(pos)
            _cellvars, pos = load(pos)
            filename, pos = load(pos)
            name, pos = load(pos)
            pos += 4  # firstlineno
            lnotab_at = pos + 5 if chr(data[pos]) in "st" else None
            lnotab, pos = load(pos)
            if not isinstance(lnotab, bytes):
                lnotab, lnotab_at = b"", None
            return CodeObject(_text(name), _text(filename), argcount, flags, code, code_at, consts,
                              [_text(n) for n in names], [_text(n) for n in varnames], lnotab, lnotab_at), pos
        raise ValueError(f"unknown marshal type {tag!r} at {pos - 1}")

    value, _ = load(0)
    if not isinstance(value, CodeObject):
        raise ValueError("not a code object")
    return value


def module_path(filename: str) -> str:
    """Script-relative module path; PC builds record the build machine's absolute path."""
    for sep in ("script\\", "script/"):
        if ":" in filename and sep in filename:
            filename = filename.split(sep, 1)[1]
    return filename.replace("\\", "/")


def load_modules(entries: Iterable[bytes]) -> dict[str, ScriptModule]:
    """{module path: ScriptModule} from decrypted entry payloads; entries that are not code objects are skipped."""
    modules = {}
    for data in entries:
        if not data or data[0] != ord("c"):
            continue
        try:
            code = load_code(data)
        except (ValueError, struct.error, IndexError, UnicodeDecodeError):
            continue
        path = module_path(code.filename)
        modules[path] = ScriptModule(path, code, data)
    return modules
