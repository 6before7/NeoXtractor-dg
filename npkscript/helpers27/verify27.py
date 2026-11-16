# Python 2.7. Recompile check: compile decompiled source and compare every code object with the original bytecode.
# Nothing is executed: compile() only. Output: one JSON line per module with per-function verdicts.
import sys, os, json, marshal, types, opcode, dis
HAVE = opcode.HAVE_ARGUMENT
JREL = set(opcode.hasjrel); JABS = set(opcode.hasjabs)
NOP = opcode.opmap['NOP']; JF = opcode.opmap['JUMP_FORWARD']

def instrs(co):
    b = co.co_code; i = 0; out = []
    while i < len(b):
        op = ord(b[i])
        if op >= HAVE:
            a = ord(b[i+1]) | ord(b[i+2]) << 8; n = 3
        else:
            a = None; n = 1
        out.append((i, op, a, n)); i += n
    return out

_NOVAL = object()


def norm(co):
    """Instruction list with jump targets as instruction indices; drops NOPs and no-op `JUMP_FORWARD 0`."""
    ins = instrs(co)
    # reachability from offset 0 (fall-through + jump targets, incl. SETUP_* handlers): the 2017 compiler omits
    # unreachable epilogues that CPython 2.7.18 emits, and vice versa
    at = dict((x[0], k) for k, x in enumerate(ins)); seen = set(); todo = [0]
    STOP = set(opcode.opmap[n] for n in ('RETURN_VALUE', 'RAISE_VARARGS', 'JUMP_ABSOLUTE', 'JUMP_FORWARD', 'CONTINUE_LOOP', 'BREAK_LOOP', 'END_FINALLY'))
    while todo:
        k = todo.pop()
        if k in seen or k >= len(ins): continue
        seen.add(k); off, op, a, n = ins[k]
        if op in JREL and (off + n + a) in at: todo.append(at[off + n + a])
        if op in JABS and a in at: todo.append(at[a])
        if op not in STOP or op == opcode.opmap['END_FINALLY']: todo.append(k + 1)
    keep = [x for k, x in enumerate(ins) if k in seen and not (x[1] == NOP or (x[1] == JF and x[2] == 0))]
    # items: [offset, name, arg, const value or _NOVAL]; jump args hold the absolute target offset for now
    LC = opcode.opmap['LOAD_CONST']; IN_OPS = (6, 7)   # COMPARE_OP `in` / `not in`
    items = []
    for i, (off, op, a, n) in enumerate(keep):
        name = opcode.opname[op]
        if op in JREL: items.append([off, name, ('->', off + n + a), _NOVAL])
        elif op in JABS: items.append([off, name, ('->', a), _NOVAL])
        elif op == LC:
            c = co.co_consts[a]
            items.append([off, name, ('code', c.co_name) if isinstance(c, types.CodeType) else None,
                          _NOVAL if isinstance(c, types.CodeType) else c])
        elif op in opcode.hasname: items.append([off, name, ('n', co.co_names[a]), _NOVAL])
        elif op in opcode.haslocal: items.append([off, name, ('v', co.co_varnames[a]), _NOVAL])
        elif op in opcode.hasfree:
            cf = co.co_cellvars + co.co_freevars
            items.append([off, name, ('f', cf[a] if a < len(cf) else a), _NOVAL])
        else: items.append([off, name, a, _NOVAL])
    # Constant folding differs between the engine's compilers and CPython 2.7.18 (Feb-May 2018 builds do not fold):
    # fold LOAD_CONST*k + BUILD_TUPLE k, and LOAD_CONST*k + BUILD_LIST k before `in`/`not in`, into one constant
    # tuple on both sides. Nested tuples fold because folded items are constants again.
    folded = []
    for i, it in enumerate(items):
        k = it[2]
        is_list_in = (it[1] == 'BUILD_LIST' and i + 1 < len(items) and items[i + 1][1] == 'COMPARE_OP'
                      and items[i + 1][2] in IN_OPS)
        if (it[1] == 'BUILD_TUPLE' or is_list_in) and isinstance(k, int) and k > 0 and len(folded) >= k \
                and all(x[1] == 'LOAD_CONST' and x[3] is not _NOVAL for x in folded[-k:]):
            vals = tuple(x[3] for x in folded[-k:])
            first = folded[-k][0]
            del folded[-k:]
            folded.append([first, 'LOAD_CONST', None, vals])
        else:
            folded.append(it)
    offs = [x[0] for x in folded]
    end = len(folded)
    import bisect
    def tgt(t):   # index of the first remaining item at or after byte offset t
        j = bisect.bisect_left(offs, t)
        return j if j < end else end
    out = []
    for off, name, arg, val in folded:
        if isinstance(arg, tuple) and arg and arg[0] == '->':
            arg = ('->', tgt(arg[1]))
        elif name == 'LOAD_CONST' and val is not _NOVAL:
            arg = ('k', type(val).__name__, repr(val))
        out.append((name, arg))
    # a closure's cell order follows the compiler's name hashing, which differs between Python builds:
    # compare each run of LOAD_CLOSURE as a set
    i = 0
    while i < len(out):
        if out[i][0] == 'LOAD_CLOSURE':
            j = i
            while j < len(out) and out[j][0] == 'LOAD_CLOSURE':
                j += 1
            out[i:j] = sorted(out[i:j])
            i = j
        else:
            i += 1
    return out

def tree(co, path=''):
    p = path + '/' + co.co_name; yield p, co
    seen = {}
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            for x in tree(c, p): yield x

def pairs(a, b):
    A = {}; B = {}
    for p, c in tree(a): A.setdefault(p, []).append(c)
    for p, c in tree(b): B.setdefault(p, []).append(c)
    return A, B

def check(orig_pyc, src_path):
    orig = marshal.loads(open(orig_pyc, 'rb').read()[8:])
    src = open(src_path).read()
    try:
        new = compile(src, orig.co_filename, 'exec')
    except Exception as e:
        new = None; err = str(e)[:200]
    A, _ = pairs(orig, orig)
    res = {}
    if new is None:
        for p in A: res[p] = 'nocompile'
        return res, err
    _, B = pairs(orig, new)
    for p, cos in A.items():
        got = B.get(p, [])
        for k, c in enumerate(cos):
            key = p if len(cos) == 1 else '%s#%d' % (p, k)
            if k >= len(got): res[key] = 'missing'; continue
            n = got[k]
            if norm(c) == norm(n) and c.co_varnames[:c.co_argcount] == n.co_varnames[:n.co_argcount]:
                res[key] = 'same'
            else:
                res[key] = 'differs'
    return res, None

if __name__ == '__main__':
    # args: <orig pyc root> <source root> <out jsonl> [relative paths file]
    oroot, sroot, outp = sys.argv[1:4]
    rels = [l.strip() for l in open(sys.argv[4])] if len(sys.argv) > 4 else None
    if rels is None:
        rels = []
        for d, _, fs in os.walk(oroot):
            for f in fs:
                if f.endswith('.pyc'): rels.append(os.path.relpath(os.path.join(d, f), oroot)[:-1])
    with open(outp, 'w') as out:
        for rel in sorted(rels):
            s = os.path.join(sroot, rel)
            if not os.path.exists(s): continue
            try: r, err = check(os.path.join(oroot, rel + 'c'), s)
            except Exception as e: r, err = {}, 'verifier: %s' % e
            out.write(json.dumps({'module': rel, 'functions': r, 'error': err}) + '\n')
