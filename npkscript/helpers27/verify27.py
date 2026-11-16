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
    # map each byte offset to the index of the first kept instruction at or after it
    idx = {}; k = 0
    for off, op, a, n in ins:
        while k < len(keep) and keep[k][0] < off: k += 1
        idx[off] = k
    end = len(keep)
    def tgt(t): return idx.get(t, end)
    out = []
    for off, op, a, n in keep:
        name = opcode.opname[op]
        if op in JREL: arg = ('->', tgt(off + n + a))
        elif op in JABS: arg = ('->', tgt(a))
        elif op == opcode.opmap['LOAD_CONST']:
            c = co.co_consts[a]
            arg = ('code', c.co_name) if isinstance(c, types.CodeType) else ('k', type(c).__name__, repr(c))
        elif op in opcode.hasname: arg = ('n', co.co_names[a])
        elif op in opcode.haslocal: arg = ('v', co.co_varnames[a])
        elif op in opcode.hasfree:
            cf = co.co_cellvars + co.co_freevars; arg = ('f', cf[a] if a < len(cf) else a)
        else: arg = a
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
