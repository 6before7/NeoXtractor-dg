# Python 2.7. Per-function selection between several decompiler outputs of one module, by recompile check.
# Nothing is executed: source is only tokenized and compile()d; bytecode is compared with the original .pyc.
import sys, os, json, marshal, types, tokenize, StringIO, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify27 as V


DUMP = re.compile(r'^(\s*(\d+(_\d+)?|L\.\s*\d+)\s+[A-Z_]{3,}|Syntax error at or near|\s*-->|\s*--- This code section)')


def logical_lines(text):
    """(start_row, indent_col, first_word) for each logical line; rows are 1-based. Error-tolerant: a small lexer
    tracks strings, comments, brackets and backslash continuations, so one bad statement cannot hide the rest."""
    out = []; depth = 0; tq = None; cont = False
    for r, line in enumerate(text.split('\n'), 1):
        if DUMP.match(line):      # decompiler failure dumps (instruction listings, error banners): not code
            continue
        starts = depth == 0 and tq is None and not cont
        if starts:
            st = line.strip()
            if st and not st.startswith('#'):
                m = re.match(r'(@|[A-Za-z_]\w*)', st)
                out.append((r, len(line) - len(line.lstrip(' ')), m.group(1) if m else st[:1]))
        i = 0; n = len(line); cont = False; sq = None
        while i < n:
            ch = line[i]
            if tq:
                if line.startswith(tq, i): i += 3; tq = None; continue
                if ch == '\\': i += 2; continue
                i += 1; continue
            if sq:
                if ch == '\\': i += 2; continue
                if ch == sq: sq = None
                i += 1; continue
            if ch == '#': break
            if line.startswith('"""', i) or line.startswith("'''", i): tq = line[i:i+3]; i += 3; continue
            if ch in '"\'': sq = ch; i += 1; continue
            if ch in '([{': depth += 1
            elif ch in ')]}': depth = max(0, depth - 1)
            i += 1
        if sq: depth = 0          # unterminated string on a broken line: resynchronise
        if line.rstrip().endswith('\\') and not tq: cont = True
    return out


def blocks(text):
    """def blocks: {(class_chain, name, occurrence): (first_row, last_row, col)}; decorators included."""
    L = logical_lines(text)
    lines = text.split('\n'); res = {}; stack = []; seen = {}
    for k, (r, c, s) in enumerate(L):
        while stack and stack[-1][1] >= c:
            stack.pop()
        if s in ('def', 'class'):
            m = re.match(r'\s*(?:def|class)\s+(\w+)', lines[r - 1])
            if not m:
                continue
            name = m.group(1)
            end = len(lines)
            for r2, c2, s2 in L[k + 1:]:
                if c2 <= c:
                    end = r2 - 1; break
            first = r
            j = k - 1
            while j >= 0 and L[j][2] == '@' and L[j][1] == c:
                first = L[j][0]; j -= 1
            chain = tuple(x[0] for x in stack if x[2] == 'class')
            if s == 'def' and not any(x[2] == 'def' for x in stack):
                key = (chain, name); n = seen.get(key, 0); seen[key] = n + 1
                res[chain + (name, n)] = (first, end, c)
            stack.append((name, c, s))
    return res


def block_text(lines, first, end, col, new_col):
    out = []
    for l in lines[first - 1:end]:
        lead = len(l) - len(l.lstrip(' '))
        out.append(' ' * new_col + l[min(lead, col):] if l.strip() else '')
    while out and not out[-1].strip():
        out.pop()
    return out


def original_functions(orig):
    """{(class_chain..., name, occurrence): code} for functions directly in the module or in (nested) classes."""
    res = {}
    def walk(co, chain):
        seen = {}
        for c in co.co_consts:
            if not isinstance(c, types.CodeType):
                continue
            if c.co_flags & 0x1:   # CO_OPTIMIZED: a function (class bodies are not optimized)
                if c.co_name.startswith('<'):
                    continue
                n = seen.get(c.co_name, 0); seen[c.co_name] = n + 1
                res[chain + (c.co_name, n)] = c
            else:                                      # class body
                walk(c, chain + (c.co_name,))
    walk(orig, ())
    return res


def same_tree(a, b):
    if V.norm(a) != V.norm(b):
        return False
    na = a.co_argcount + bool(a.co_flags & 4) + bool(a.co_flags & 8)
    nb = b.co_argcount + bool(b.co_flags & 4) + bool(b.co_flags & 8)
    if a.co_varnames[:na] != b.co_varnames[:nb] or (a.co_flags & 12) != (b.co_flags & 12):
        return False
    ca = [c for c in a.co_consts if isinstance(c, types.CodeType)]
    cb = [c for c in b.co_consts if isinstance(c, types.CodeType)]
    return len(ca) == len(cb) and all(same_tree(x, y) for x, y in zip(ca, cb))


def verify_block(src_lines, key, orig_fn, filename):
    chain, name = key[:-2], key[-2]
    depth = len(chain)
    text = []
    for i, cname in enumerate(chain):
        text.append('    ' * i + 'class %s:' % cname)
    text += src_lines
    try:
        co = compile('\n'.join(text) + '\n', filename, 'exec')
    except Exception:
        return 'nocompile'
    for _ in chain:
        co = [c for c in co.co_consts if isinstance(c, types.CodeType)][0]
    fns = [c for c in co.co_consts if isinstance(c, types.CodeType) and c.co_name == name]
    if not fns:
        return 'missing'
    return 'same' if same_tree(orig_fn, fns[-1]) else 'differs'


STR_LIT = re.compile(r"""(?<![\w'"])([bB]?)('|")((?:\\.|(?!\2)[^\\\n])*)\2""")
EXIT_ELSE = re.compile(r'^\s+(?:return|raise|continue|break)\b[^\n]*\n\s*else:\s*$', re.M)
ESC_RUN = re.compile(r'(?:\\x[89a-fA-F][0-9a-fA-F])+')


def unescape_utf8(text):
    """Byte-string literals spelled as \\xNN escapes that form valid UTF-8 (uncompyle2 prints Chinese that way) are
    rewritten as the characters themselves. Python 2 compiles both spellings of a plain str literal to the same bytes;
    unicode (u'') and raw (r'') literals are left alone. Verification re-checks every function afterwards."""
    def lit(m):
        body = m.group(3)
        def run(r):
            raw = r.group(0)
            try:
                b = ''.join(chr(int(raw[i + 2:i + 4], 16)) for i in range(0, len(raw), 4))
                b.decode('utf-8')
                return b
            except (UnicodeDecodeError, ValueError):
                return raw
        # an escape run must not start right after a backslash that is itself escaped (\\x41 is a literal backslash)
        out = []; i = 0
        for r in ESC_RUN.finditer(body):
            pre = body[i:r.start()]
            bs = len(pre) - len(pre.rstrip('\\'))
            out.append(pre); out.append(run(r) if bs % 2 == 0 else r.group(0)); i = r.end()
        out.append(body[i:])
        return m.group(1) + m.group(2) + ''.join(out) + m.group(2)
    return STR_LIT.sub(lit, text)


def strip_trailing_returns(text):
    """Remove a bare `return` that is the last statement of a function body (uncompyle6 prints the implicit
    `return None`); a body left empty gets `pass`. Same bytecode either way; verification re-checks."""
    lines = text.split('\n'); L = logical_lines(text); drop = {}
    for k, (r, c, w) in enumerate(L):
        if w != 'def' or k + 1 >= len(L) or L[k + 1][1] <= c:
            continue
        body_col = L[k + 1][1]; last = None
        for r2, c2, w2 in L[k + 1:]:
            if c2 <= c:
                break
            last = (r2, c2)
        if last and last[1] == body_col and lines[last[0] - 1].strip() == 'return':
            only = all(c2 > body_col or r2 == last[0] for r2, c2, w2 in L[k + 1:] if c2 > c and r2 <= last[0]) and \
                sum(1 for r2, c2, w2 in L[k + 1:] if c2 == body_col and r2 <= last[0]) == 1
            drop[last[0]] = ' ' * body_col + 'pass' if only else None
    out = []
    for i, l in enumerate(lines, 1):
        if i in drop:
            if drop[i] is not None:
                out.append(drop[i])
            continue
        out.append(l)
    return '\n'.join(out)


def clean(text):
    return strip_trailing_returns(unescape_utf8(text))


def noise(src_lines):
    """Readability penalty of a function's text: `pass` + `else:` (a `continue` decompiled as nesting), an `else:`
    after return/raise/continue/break, leftover escaped strings, length."""
    t = '\n'.join(src_lines)
    return (5 * len(re.findall(r'^(\s+)pass\n\s*else:', t, re.M)) + 3 * len(EXIT_ELSE.findall(t))
            + 2 * len(ESC_RUN.findall(t)) + 0.02 * len(src_lines))


def select(orig_pyc, cands, filename=None):
    orig = marshal.loads(open(orig_pyc, 'rb').read()[8:])
    filename = filename or orig.co_filename
    O = original_functions(orig)
    texts = []
    for name, path in cands:
        if os.path.exists(path):
            t = clean(open(path).read())
            texts.append((name, t, t.split('\n'), blocks(t)))
    verdict = {}   # key -> {cand: verdict}
    for key, fn in O.items():
        verdict[key] = {}
        for name, t, lines, B in texts:
            if B is None or key not in B:
                verdict[key][name] = 'missing'; continue
            first, end, col = B[key]
            verdict[key][name] = verify_block(block_text(lines, first, end, col, 4 * (len(key) - 2)), key, fn, filename)
    # base: a candidate whose whole module compiles, preferring the most verified functions, then priority order
    def compiles(t):
        try:
            compile(t, filename, 'exec'); return True
        except Exception:
            return False
    order = []
    for i, (name, t, lines, B) in enumerate(texts):
        good = sum(1 for k in O if verdict[k].get(name) == 'same')
        order.append((not compiles(t), i))   # priority order (most readable first); splicing fixes the rest
    order.sort()
    base_i = order[0][-1]
    bname, bt, blines, bB = texts[base_i]
    result = list(blines); chosen = {}
    if bB is not None:
        # replace from the bottom up so earlier row numbers stay valid
        repl = []
        for key in O:
            ok = [x for x in texts if verdict[key].get(x[0]) == 'same']
            if key not in bB or not ok:
                chosen[key] = bname if verdict[key].get(bname) == 'same' else None; continue
            bfirst, bend, bcol = bB[key]
            scored = []
            for prio, (n, t, l, B) in enumerate(ok):
                first, end, col = B[key]
                txt = block_text(l, first, end, col, bcol)
                scored.append((noise(txt), n != bname, prio, n, txt))
            scored.sort()
            _, _, _, n, txt = scored[0]
            if n == bname:
                chosen[key] = bname; continue
            repl.append((bfirst, bend, txt, key, n))
        for bfirst, bend, new, key, n in sorted(repl, reverse=True):
            trial = result[:bfirst - 1] + new + [''] + result[bend:]
            if compiles('\n'.join(trial)) or not compiles('\n'.join(result)):
                result = trial; chosen[key] = n
            else:
                chosen[key] = bname if verdict[key].get(bname) == 'same' else None
    final = '\n'.join(result)
    # flag functions whose bytecode could not be verified, directly above their def (or decorators)
    FB = blocks(final); fl = final.split('\n')
    for key in sorted((k for k in O if not chosen.get(k) and k in FB), key=lambda k: -FB[k][0]):
        first, end, col = FB[key]
        fl.insert(first - 1, ' ' * col + '# [npk_decompile] UNVERIFIED: recompiling this function does not reproduce the '
                  'original bytecode; read with care')
    final = '\n'.join(fl)
    # final check over the assembled module (all code objects, incl. module and class bodies)
    tmp_res = {}
    try:
        new = compile(final, filename, 'exec'); ok = True
    except Exception as e:
        ok = False
    fin = {}
    for key, fn in O.items():
        fin['/'.join(key[:-1]) + ('' if key[-1] == 0 else '#%d' % key[-1])] = dict(verdict=verdict[key], chosen=chosen.get(key))
    verified = sum(1 for k in O if chosen.get(k))
    return final, dict(base=bname, compiles=ok, functions=len(O), verified=verified, detail=fin)


if __name__ == '__main__':
    # args: <orig pyc root> <out source root> <report jsonl> <rel list file> name=root [name=root ...]
    oroot, out_root, rep, rel_file = sys.argv[1:5]
    cand_roots = [a.split('=', 1) for a in sys.argv[5:]]
    with open(rep, 'w') as R:
        for rel in open(rel_file).read().split():
            pyc = os.path.join(oroot, rel + 'c')
            try:
                text, info = select(pyc, [(n, os.path.join(r, rel)) for n, r in cand_roots])
            except Exception as e:
                R.write(json.dumps(dict(module=rel, error=repr(e)[:300])) + '\n'); continue
            dst = os.path.join(out_root, rel)
            if not os.path.isdir(os.path.dirname(dst)):
                os.makedirs(os.path.dirname(dst))
            open(dst, 'w').write(text)
            info['module'] = rel
            R.write(json.dumps(info) + '\n')
