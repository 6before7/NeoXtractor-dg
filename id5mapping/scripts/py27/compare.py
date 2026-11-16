# -*- coding: utf-8 -*-
"""Compare Python 2.7 code objects by behaviour-relevant bytecode (runs under Python 2.7).

The game's 2017-2018 compiler and CPython 2.7.18 emit slightly different bytecode for the same source. The
comparison ignores exactly those differences, none of which change behaviour:

* unreachable code (each compiler drops or keeps different dead epilogues), NOPs and ``JUMP_FORWARD 0``;
* constant folding: ``LOAD_CONST``*k + ``BUILD_TUPLE k`` (and a constant list before ``in``/``not in``) versus one
  constant tuple; the Feb-May 2018 compiler does not fold, CPython 2.7.18 does;
* the order of closure cells, which follows each interpreter's string hashing.

Jump targets are compared as instruction indices, names and constants by value.
"""

import bisect
import opcode
import types

HAVE_ARGUMENT = opcode.HAVE_ARGUMENT
RELATIVE = set(opcode.hasjrel)
ABSOLUTE = set(opcode.hasjabs)
NOP = opcode.opmap['NOP']
JUMP_FORWARD = opcode.opmap['JUMP_FORWARD']
LOAD_CONST = opcode.opmap['LOAD_CONST']
FLOW_ENDS = set(opcode.opmap[name] for name in (
    'RETURN_VALUE', 'RAISE_VARARGS', 'JUMP_ABSOLUTE', 'JUMP_FORWARD', 'CONTINUE_LOOP', 'BREAK_LOOP'))
COMPARE_IN = (6, 7)  # COMPARE_OP arguments of `in` and `not in`
CO_VARARGS, CO_VARKEYWORDS = 0x4, 0x8
_NO_VALUE = object()


def _instructions(code):
    """[(offset, opcode, arg or None, size)]."""
    raw, out, i = code.co_code, [], 0
    while i < len(raw):
        op = ord(raw[i])
        if op >= HAVE_ARGUMENT:
            out.append((i, op, ord(raw[i + 1]) | ord(raw[i + 2]) << 8, 3))
            i += 3
        else:
            out.append((i, op, None, 1))
            i += 1
    return out


def _reachable(ins):
    """Indexes of instructions reachable from the start (fall-through and every jump target, handlers included)."""
    index_at = dict((offset, k) for k, (offset, _, _, _) in enumerate(ins))
    seen, todo = set(), [0]
    while todo:
        k = todo.pop()
        if k in seen or k >= len(ins):
            continue
        seen.add(k)
        offset, op, arg, size = ins[k]
        if op in RELATIVE and offset + size + arg in index_at:
            todo.append(index_at[offset + size + arg])
        if op in ABSOLUTE and arg in index_at:
            todo.append(index_at[arg])
        if op not in FLOW_ENDS:
            todo.append(k + 1)
    return seen


def _item(code, offset, op, arg, size):
    """[offset, name, comparable argument, constant value or _NO_VALUE]; jump arguments are target offsets."""
    name = opcode.opname[op]
    if op in RELATIVE:
        return [offset, name, ('->', offset + size + arg), _NO_VALUE]
    if op in ABSOLUTE:
        return [offset, name, ('->', arg), _NO_VALUE]
    if op == LOAD_CONST:
        value = code.co_consts[arg]
        if isinstance(value, types.CodeType):
            return [offset, name, ('code', value.co_name), _NO_VALUE]
        return [offset, name, None, value]
    if op in opcode.hasname:
        return [offset, name, ('n', code.co_names[arg]), _NO_VALUE]
    if op in opcode.haslocal:
        return [offset, name, ('v', code.co_varnames[arg]), _NO_VALUE]
    if op in opcode.hasfree:
        cells = code.co_cellvars + code.co_freevars
        return [offset, name, ('f', cells[arg] if arg < len(cells) else arg), _NO_VALUE]
    return [offset, name, arg, _NO_VALUE]


def _fold_constants(items):
    folded = []
    for i, item in enumerate(items):
        count = item[2]
        list_before_in = (item[1] == 'BUILD_LIST' and i + 1 < len(items) and items[i + 1][1] == 'COMPARE_OP'
                          and items[i + 1][2] in COMPARE_IN)
        if ((item[1] == 'BUILD_TUPLE' or list_before_in) and isinstance(count, int) and 0 < count <= len(folded)
                and all(x[1] == 'LOAD_CONST' and x[3] is not _NO_VALUE for x in folded[-count:])):
            values = tuple(x[3] for x in folded[-count:])
            first_offset = folded[-count][0]
            del folded[-count:]
            folded.append([first_offset, 'LOAD_CONST', None, values])
        else:
            folded.append(item)
    return folded


def _sort_closure_runs(out):
    i = 0
    while i < len(out):
        if out[i][0] != 'LOAD_CLOSURE':
            i += 1
            continue
        j = i
        while j < len(out) and out[j][0] == 'LOAD_CLOSURE':
            j += 1
        out[i:j] = sorted(out[i:j])
        i = j


def normalize(code):
    """[(name, comparable argument)] for one code object (nested code objects are compared separately)."""
    ins = _instructions(code)
    seen = _reachable(ins)
    kept = [x for k, x in enumerate(ins)
            if k in seen and x[1] != NOP and not (x[1] == JUMP_FORWARD and x[2] == 0)]
    items = _fold_constants([_item(code, *x) for x in kept])
    offsets = [x[0] for x in items]

    def target_index(offset):  # the first remaining instruction at or after a byte offset
        return min(bisect.bisect_left(offsets, offset), len(items))

    out = []
    for _, name, arg, value in items:
        if isinstance(arg, tuple) and arg and arg[0] == '->':
            arg = ('->', target_index(arg[1]))
        elif name == 'LOAD_CONST' and value is not _NO_VALUE:
            arg = ('k', type(value).__name__, repr(value))
        out.append((name, arg))
    _sort_closure_runs(out)
    return out


def _signature(code):
    n = code.co_argcount + bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
    return code.co_varnames[:n], code.co_flags & (CO_VARARGS | CO_VARKEYWORDS)


def same_function(a, b):
    """True when two code objects, and all code nested in them, behave the same."""
    if normalize(a) != normalize(b) or _signature(a) != _signature(b):
        return False
    nested_a = [c for c in a.co_consts if isinstance(c, types.CodeType)]
    nested_b = [c for c in b.co_consts if isinstance(c, types.CodeType)]
    return len(nested_a) == len(nested_b) and all(same_function(x, y) for x, y in zip(nested_a, nested_b))


def functions_of(module_code):
    """{(class, ..., name, occurrence): code} for functions defined at module level or in (nested) classes."""
    found = {}

    def walk(code, classes):
        seen = {}
        for const in code.co_consts:
            if not isinstance(const, types.CodeType):
                continue
            if const.co_flags & 0x1:  # CO_OPTIMIZED: a function; class bodies are not optimized
                if const.co_name.startswith('<'):
                    continue
                occurrence = seen.get(const.co_name, 0)
                seen[const.co_name] = occurrence + 1
                found[classes + (const.co_name, occurrence)] = const
            else:
                walk(const, classes + (const.co_name,))
    walk(module_code, ())
    return found
