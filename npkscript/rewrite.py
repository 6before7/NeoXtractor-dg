"""Rewrite a NeoX module's bytecode to CPython 2.7 numbering and wrap it as a .pyc (Python 3, static only).

Code objects whose opcodes map one-for-one are patched in place: only opcode bytes change, so names, constants and
line tables stay exactly as shipped. Code objects with fused instructions (one opcode standing for two, Feb/Mar
2018) are reassembled: each fused opcode is expanded into its two instructions, every jump is retargeted and the
line-number table is remapped, then the code and line table are spliced back into the marshal stream. A code object
that cannot be relocated exactly (an EXTENDED_ARG next to a fused opcode, an argument that would overflow) raises
`Unsupported` instead of being approximated.
"""
from .opcodes import CPY27, HAVE_ARGUMENT, JABS, JREL, Fused, Paired, instructions

PYC_HEADER = bytes.fromhex('03f30d0a') + b'\0\0\0\0'   # Python 2.7 magic + zero mtime


class Unsupported(Exception):
    pass


def _names(code, mapping):
    """[(old offset, old size, [(name, arg), ...])]: each original instruction and what it stands for."""
    out = []
    prev = None
    run = 0
    for off, op, arg in instructions(code):
        run = run + 1 if op == prev else 0
        prev = op
        n = mapping.get(op)
        size = 3 if op >= HAVE_ARGUMENT else 1
        if n is None:
            raise Unsupported('unmapped opcode %d at %d' % (op, off))
        if isinstance(n, Fused):
            out.append((off, size, n.expand(arg)))
        elif isinstance(n, Paired):
            out.append((off, size, [(n[run % 2], arg)]))
        else:
            out.append((off, size, [(n, arg)]))
    return out


def _dead_jumps(ins):
    """[(name, arg)] -> same, with an unreachable `JUMP_FORWARD 0` right after a backward JUMP_ABSOLUTE turned into
    NOPs (the 2017 compiler's `continue` inside an `if`; it confuses uncompyle6). Offsets are unchanged."""
    out = []
    off = 0
    prev = None
    for name, arg in ins:
        size = 3 if CPY27[name] >= HAVE_ARGUMENT else 1
        if name == 'JUMP_FORWARD' and arg == 0 and prev and prev[0] == 'JUMP_ABSOLUTE' and prev[1] < off:
            out += [('NOP', None)] * 3
        else:
            out.append((name, arg))
        prev = (name, arg)
        off += size
    return out


def _assemble(ins):
    b = bytearray()
    for name, arg in ins:
        op = CPY27[name]
        if op >= HAVE_ARGUMENT:
            if arg is None or not 0 <= arg <= 0xFFFF:
                raise Unsupported('argument %r for %s' % (arg, name))
            b += bytes((op, arg & 0xFF, arg >> 8))
        else:
            b.append(op)
    return bytes(b)


def _relocate(entries):
    """Expand fused instructions and retarget jumps. Returns ([(name, arg)], {old offset: new offset})."""
    if any(name == 'EXTENDED_ARG' for _, _, parts in entries for name, _ in parts):
        raise Unsupported('EXTENDED_ARG in a code object that needs relocation')
    new_off = {}
    pos = 0
    for off, size, parts in entries:
        new_off[off] = pos
        pos += sum(3 if CPY27[n] >= HAVE_ARGUMENT else 1 for n, _ in parts)
    end_old = entries[-1][0] + entries[-1][1] if entries else 0
    new_off[end_old] = pos
    out = []
    for off, size, parts in entries:
        if len(parts) == 1 and parts[0][0] in JREL | JABS:
            name, arg = parts[0]
            target = off + size + arg if name in JREL else arg
            if target not in new_off:
                raise Unsupported('jump into the middle of an instruction at %d' % off)
            if name in JREL:
                arg = new_off[target] - (new_off[off] + 3)
            else:
                arg = new_off[target]
            out.append((name, arg))
        else:
            if any(n in JREL | JABS for n, _ in parts):
                raise Unsupported('jump inside a fused instruction at %d' % off)
            out += parts
    return out, new_off


def _lnotab(raw, new_off):
    """Remap a line-number table to relocated offsets."""
    points = []           # (address, line increment) where the line changes
    addr = 0
    for i in range(0, len(raw) - 1, 2):
        addr += raw[i]
        if raw[i + 1]:
            points.append((addr, raw[i + 1]))
    known = sorted(new_off)
    out = bytearray()
    last = 0
    for a, dl in points:
        na = new_off.get(a)
        if na is None:     # not an instruction start: the next instruction start at or after it
            na = new_off[min((k for k in known if k >= a), default=known[-1])]
        da = na - last
        while da > 255:
            out += bytes((255, 0)); da -= 255
        while dl > 255:
            out += bytes((da, 255)); dl -= 255; da = 0
        out += bytes((da, dl))
        last = na
    return bytes(out)


def to_pyc(module, mapping, dead_jumps=False):
    """The module as a CPython 2.7 .pyc (bytes)."""
    data = bytearray(module.data)
    edits = []   # (start, end, replacement) on the marshal stream
    for _, c in module.code.walk():
        entries = _names(c.code, mapping)
        fused = any(len(parts) > 1 for _, _, parts in entries)
        if fused:
            ins, new_off = _relocate(entries)
        else:
            ins = [p for _, _, parts in entries for p in parts]
        if dead_jumps:
            ins = _dead_jumps(ins)
        code = _assemble(ins)
        if not fused:
            if len(code) != len(c.code):
                raise Unsupported('in-place rewrite changed the length')
            edits.append((c.code_at, c.code_at + len(c.code), code))
            continue
        edits.append((c.code_at - 4, c.code_at + len(c.code), len(code).to_bytes(4, 'little') + code))
        if c.lnotab_at is not None:
            lt = _lnotab(c.lnotab, new_off)
            edits.append((c.lnotab_at - 4, c.lnotab_at + len(c.lnotab), len(lt).to_bytes(4, 'little') + lt))
    for start, end, rep in sorted(edits, reverse=True):
        data[start:end] = rep
    return PYC_HEADER + bytes(data)


def unmapped(module, mapping):
    return sorted({op for _, c in module.code.walk() for _, op, _ in instructions(c.code) if op not in mapping})
