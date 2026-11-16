"""Turn a script module into a CPython 2.7 ``.pyc`` using a recovered opcode map (see ``opmap.py``).

Code objects whose opcodes map one-for-one are patched in place: only opcode bytes change, so names, constants
and line tables stay exactly as shipped. Code objects with fused opcodes (one opcode for two instructions,
Feb-May 2018) are reassembled: each fused opcode is expanded, every jump is retargeted and the line table is
remapped, then code and line table are spliced back into the marshal stream. Code that cannot be relocated
exactly raises ``RewriteError`` instead of being approximated.
"""

from __future__ import annotations

from .bytecode import CPY27, HAVE_ARGUMENT, JUMP_ABSOLUTE, JUMP_RELATIVE, CodeObject, ScriptModule, instructions
from .opmap import expand_fused

PYC_HEADER = bytes.fromhex("03f30d0a") + bytes(4)  # Python 2.7 magic, zero timestamp
_JUMPS = JUMP_RELATIVE | JUMP_ABSOLUTE


class RewriteError(ValueError):
    """The module cannot be rewritten exactly."""


def _size(name: str) -> int:
    return 3 if CPY27[name] >= HAVE_ARGUMENT else 1


def unmapped_opcodes(module: ScriptModule, mapping: dict) -> list[int]:
    """Opcodes the module uses that the map does not cover; such a module cannot be rewritten."""
    return sorted({op for _, c in module.code.walk() for _, op, _ in instructions(c.code) if op not in mapping})


def _translate(code: bytes, mapping: dict) -> list[tuple[int, int, list[tuple[str, int | None]]]]:
    """(offset, size, [(name, arg)...]) for each original instruction; a fused opcode yields two instructions and a
    paired one is resolved by its position in a run of the same opcode."""
    out, prev, run = [], None, 0
    for offset, op, arg in instructions(code):
        run = run + 1 if op == prev else 0
        prev = op
        value = mapping.get(op)
        if value is None:
            raise RewriteError(f"unmapped opcode {op} at offset {offset}")
        size = 3 if op >= HAVE_ARGUMENT else 1
        if isinstance(value, str):
            out.append((offset, size, [(value, arg)]))
        elif value[0] == "pair":
            out.append((offset, size, [(value[1 + run % 2], arg)]))
        else:
            out.append((offset, size, expand_fused(value, arg)))
    return out


def _relocate(translated) -> tuple[list[tuple[str, int | None]], dict[int, int]]:
    """Flatten translated instructions with retargeted jumps; returns them and {old offset: new offset}."""
    if any(name == "EXTENDED_ARG" for _, _, parts in translated for name, _ in parts):
        raise RewriteError("EXTENDED_ARG in code that needs relocation")
    new_offset, position = {}, 0
    for offset, _, parts in translated:
        new_offset[offset] = position
        position += sum(_size(name) for name, _ in parts)
    if translated:
        last_offset, last_size, _ = translated[-1]
        new_offset[last_offset + last_size] = position
    out = []
    for offset, size, parts in translated:
        if len(parts) > 1 and any(name in _JUMPS for name, _ in parts):
            raise RewriteError(f"jump inside a fused instruction at {offset}")
        name, arg = parts[0]
        if len(parts) == 1 and name in _JUMPS:
            target = offset + size + arg if name in JUMP_RELATIVE else arg
            if target not in new_offset:
                raise RewriteError(f"jump into the middle of an instruction at {offset}")
            arg = new_offset[target] - (new_offset[offset] + 3) if name in JUMP_RELATIVE else new_offset[target]
            out.append((name, arg))
        else:
            out += parts
    return out, new_offset


def _blank_dead_jumps(ins: list[tuple[str, int | None]]) -> list[tuple[str, int | None]]:
    """Turn the unreachable ``JUMP_FORWARD 0`` that follows a backward JUMP_ABSOLUTE (the 2017 compiler's
    ``continue`` inside ``if``) into NOPs; uncompyle6 misreads it. Offsets do not change."""
    out, offset = [], 0
    prev_name, prev_arg = None, None
    for name, arg in ins:
        if name == "JUMP_FORWARD" and arg == 0 and prev_name == "JUMP_ABSOLUTE" and prev_arg < offset:
            out += [("NOP", None)] * 3
        else:
            out.append((name, arg))
        prev_name, prev_arg = name, arg
        offset += _size(name)
    return out


def _assemble(ins) -> bytes:
    code = bytearray()
    for name, arg in ins:
        op = CPY27[name]
        if op < HAVE_ARGUMENT:
            code.append(op)
        elif arg is None or not 0 <= arg <= 0xFFFF:
            raise RewriteError(f"argument {arg!r} for {name}")
        else:
            code += bytes((op, arg & 0xFF, arg >> 8))
    return bytes(code)


def _remap_lnotab(lnotab: bytes, new_offset: dict[int, int]) -> bytes:
    """A line-number table for relocated code: each line change moves to its instruction's new offset."""
    changes, address = [], 0
    for i in range(0, len(lnotab) - 1, 2):
        address += lnotab[i]
        if lnotab[i + 1]:
            changes.append((address, lnotab[i + 1]))
    starts = sorted(new_offset)
    out, last = bytearray(), 0
    for address, line_step in changes:
        moved = new_offset.get(address)
        if moved is None:  # not an instruction start: the next one
            moved = new_offset[min((s for s in starts if s >= address), default=starts[-1])]
        step = moved - last
        while step > 255:
            out += bytes((255, 0))
            step -= 255
        while line_step > 255:
            out += bytes((step, 255))
            line_step -= 255
            step = 0
        out += bytes((step, line_step))
        last = moved
    return bytes(out)


def _length_prefixed(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "little") + payload


def _edits(code: CodeObject, mapping: dict, blank_dead_jumps: bool) -> list[tuple[int, int, bytes]]:
    translated = _translate(code.code, mapping)
    if all(len(parts) == 1 for _, _, parts in translated):  # same length: patch opcode bytes in place
        ins = [part for _, _, parts in translated for part in parts]
        new = _assemble(_blank_dead_jumps(ins) if blank_dead_jumps else ins)
        return [(code.code_at, code.code_at + len(code.code), new)]
    ins, new_offset = _relocate(translated)
    new = _assemble(_blank_dead_jumps(ins) if blank_dead_jumps else ins)
    edits = [(code.code_at - 4, code.code_at + len(code.code), _length_prefixed(new))]
    if code.lnotab_at is not None:
        lnotab = _remap_lnotab(code.lnotab, new_offset)
        edits.append((code.lnotab_at - 4, code.lnotab_at + len(code.lnotab), _length_prefixed(lnotab)))
    return edits


def to_pyc(module: ScriptModule, mapping: dict, blank_dead_jumps: bool = False) -> bytes:
    """The module as a CPython 2.7 .pyc."""
    data = bytearray(module.data)
    edits = [e for _, code in module.code.walk() for e in _edits(code, mapping, blank_dead_jumps)]
    for start, end, replacement in sorted(edits, reverse=True):  # back to front keeps earlier offsets valid
        data[start:end] = replacement
    return PYC_HEADER + bytes(data)
