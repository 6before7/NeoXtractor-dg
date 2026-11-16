"""Recover a NeoX build's opcode permutation statically (Python 3).

1. Alignment: the client bundles its own copy of the Python 2.7 standard library under `lib/`. Every bundled
   function whose names, local names and bytecode length equal the same function compiled by CPython 2.7.18
   (data/cpython27_stdlib_ref.json.gz) and whose operands match byte for byte pins its opcodes: each permuted
   opcode votes for the standard name at the same offset. An opcode with conflicting votes is left unmapped.
2. Comprehension rule: an opcode found only inside `<dictcomp>` (or `<setcomp>`) code objects, always with
   argument 2, is MAP_ADD (SET_ADD), if that name is still free.
2b. Optionally (`align_with`), the same alignment against the game code of builds already mapped completely: game
   functions that did not change between builds pin opcodes the standard library never uses.
3. Known maps (data/known_maps.json): a known map of this engine family that agrees with every opcode found so
   far (same opcode -> same name, no name on two opcodes) fills in the rest. In step 1 a split vote is accepted
   only with at least 10 votes and a 5:1 majority (stdlib functions that changed between 2.7 releases add noise). The source of each opcode is recorded.
Opcodes may be aliases (two opcodes for one instruction) or paired super-instructions (an opcode that appears twice
in a row standing for two instructions, as in Feb/Mar 2018: `(LOAD_FAST, LOAD_ATTR)`); a paired opcode maps to a
tuple (first, second) and is resolved by its position within a run.
Fused instructions (one opcode for two instructions, arguments packed into its high and low byte, as Feb/Mar 2018's
LOAD_FAST+LOAD_CONST) are found by a sequence diff against an already-mapped build (`align_with`); they map to a
`Fused` value and are expanded when the code is rewritten.
Anything still unmapped is reported; code objects that use it are refused rather than guessed.
"""
import collections
import difflib
import gzip
import json
import os

from .opcodes import CPY27, Fused, Paired, instructions

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def _plain(consts):
    return [repr(x) for x in consts if type(x).__name__ != 'Code']


def _prefix(a, b):
    """True when one constant list extends the other: compilers of a family sometimes fold an extra constant, which
    leaves every shared constant index (and so every voted LOAD_CONST argument) unchanged."""
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _paths(code):
    out = collections.defaultdict(list)
    for p, c in code.walk():
        out[p].append(c)
    return out


def derive(mods, log=print, align_with=()):
    ref = json.load(gzip.open(os.path.join(DATA, 'cpython27_stdlib_ref.json.gz')))['modules']
    opname = {v: k for k, v in CPY27.items()}
    votes = collections.defaultdict(collections.Counter)
    pvotes = collections.defaultdict(collections.Counter)   # (opcode, position in a run of that opcode % 2)
    aligned = 0
    for path, m in mods.items():
        if not path.startswith('lib/') or path[4:] not in ref:
            continue
        R = collections.defaultdict(list)
        for p, names, varnames, hexcode in ref[path[4:]]:
            R[p].append((names, varnames, bytes.fromhex(hexcode)))
        for p, cos in _paths(m.code).items():
            if len(cos) != 1 or len(R.get(p, ())) != 1:
                continue
            c = cos[0]; names, varnames, rc = R[p][0]
            if len(rc) != len(c.code) or names != c.names or varnames != c.varnames:
                continue
            local, ok, i, prev, run = [], True, 0, None, 0
            while i < len(rc):
                a, b = c.code[i], rc[i]
                if (a >= 90) != (b >= 90) or b not in opname:
                    ok = False; break
                if b >= 90:
                    if c.code[i + 1:i + 3] != rc[i + 1:i + 3]:
                        ok = False; break
                    i += 3
                else:
                    i += 1
                run = run + 1 if a == prev else 0
                prev = a
                local.append((a, opname[b], run % 2))
            if ok:
                aligned += 1
                for a, n, parity in local:
                    votes[a][n] += 1
                    pvotes[(a, parity)][n] += 1
    # optional: game code of already-mapped builds (same functions compiled by the same engine family)
    for other, omap in align_with:
        n_al = 0
        for path, m in mods.items():
            o = other.get(path)
            if o is None or path.startswith('lib/'):
                continue
            A = _paths(m.code); B = _paths(o.code)
            for p, cos in A.items():
                if len(cos) != 1 or len(B.get(p, ())) != 1:
                    continue
                c, d = cos[0], B[p][0]
                if len(c.code) != len(d.code) or c.names != d.names or c.varnames != d.varnames:
                    continue
                local, ok, i, prev, run = [], True, 0, None, 0
                while i < len(d.code):
                    a, b = c.code[i], d.code[i]
                    nb = omap.get(b)
                    if (a >= 90) != (b >= 90) or not isinstance(nb, str):
                        ok = False; break
                    if b >= 90:
                        if c.code[i + 1:i + 3] != d.code[i + 1:i + 3]:
                            ok = False; break
                        i += 3
                    else:
                        i += 1
                    run = run + 1 if a == prev else 0
                    prev = a
                    local.append((a, nb, run % 2))
                if ok:
                    n_al += 1
                    for a, n, parity in local:
                        votes[a][n] += 1
                        pvotes[(a, parity)][n] += 1
        log('alignment with a mapped build: %d functions matched' % n_al)
    # Fused instructions change code length, so the exact alignment above cannot see them. A sequence diff of
    # functions that are otherwise identical (names, local names, constants) against a mapped build finds, for an
    # opcode not yet known, either a one-for-one replacement (a vote, as above) or one opcode standing for two
    # instructions whose arguments it packs as high and low byte.
    fused_votes = collections.defaultdict(collections.Counter)

    def diff_pass(fused_known):
        """One sequence-diff pass. Opcodes in `fused_known` are expanded into their two instructions first, so a rare
        opcode sitting next to a fused one can still be matched one-for-one."""
        known_now = {op for op, c in votes.items() if len(c) == 1}
        trans = {op: votes[op].most_common(1)[0][0] for op in known_now}
        for other, omap in align_with:
            for path, m in mods.items():
                o = other.get(path)
                if o is None:
                    continue
                A = _paths(m.code); B = _paths(o.code)
                for p, cos in A.items():
                    if len(cos) != 1 or len(B.get(p, ())) != 1:
                        continue
                    c, d = cos[0], B[p][0]
                    ins_c = list(instructions(c.code))
                    if all(op in known_now or op in fused_known for _, op, _ in ins_c):
                        continue
                    if c.names != d.names or c.varnames != d.varnames or not _prefix(_plain(c.consts), _plain(d.consts)):
                        continue
                    ins_d = [(omap.get(op), a) for _, op, a in instructions(d.code)]
                    if any(not isinstance(n, str) for n, _ in ins_d):
                        continue
                    units = []          # (opcode or None for a fused half, arg, name if known)
                    for _, op, a in ins_c:
                        if op in fused_known:
                            units += [(None, x, n) for n, x in fused_known[op].expand(a)]
                        else:
                            units.append((op, a, trans.get(op)))
                    sa = [n or '?%d' % op for op, _, n in units]
                    sb = [n for n, _ in ins_d]
                    sm = difflib.SequenceMatcher(None, sa, sb, autojunk=False)
                    for tag, i1, i2, j1, j2 in sm.get_opcodes():
                        if tag != 'replace':
                            continue
                        seg = units[i1:i2]; ref = ins_d[j1:j2]
                        if all(n for _, _, n in seg):
                            continue
                        if len(seg) == len(ref):
                            for (op, a, n0), (n, b) in zip(seg, ref):
                                if n0 is None and op is not None and (a is None) == (b is None) and a == b:
                                    votes[op][n] += 1
                        elif not fused_known and len(seg) == 1 and len(ref) == 2 and seg[0][1] is not None \
                                and None not in (ref[0][1], ref[1][1]):
                            op, a = seg[0][0], seg[0][1]
                            if a == (ref[0][1] << 8 | ref[1][1]):
                                fused_votes[op][(ref[0][0], ref[1][0], 'hi/lo')] += 1
                            elif a == (ref[1][1] << 8 | ref[0][1]):
                                fused_votes[op][(ref[0][0], ref[1][0], 'lo/hi')] += 1

    if align_with:
        diff_pass({})
        provisional = {op: Fused(*c.most_common(1)[0][0]) for op, c in fused_votes.items()
                       if len(c) == 1 and c.most_common(1)[0][1] >= 10 and op not in votes}
        if provisional:
            diff_pass(provisional)
    mapping, source, conflicts = {}, {}, {}
    for op, c in votes.items():
        if len(c) == 1:
            mapping[op] = next(iter(c)); source[op] = 'alignment (%d votes)' % c[mapping[op]]
        else:
            (n1, v1), (n2, v2) = c.most_common(2)
            rest = sum(c.values()) - v1
            first, second = pvotes[(op, 0)], pvotes[(op, 1)]
            if v1 >= 10 and v1 >= 5 * rest:   # stdlib functions that changed between 2.7 releases vote a little noise
                mapping[op] = n1; source[op] = 'alignment (majority %d:%d)' % (v1, rest)
            elif (len(first) == 1 and len(second) == 1 and first.most_common(1)[0][1] >= 10
                  and second.most_common(1)[0][1] >= 10 and next(iter(first)) != next(iter(second))):
                # a paired super-instruction: the opcode appears twice in a row, standing for two instructions
                mapping[op] = Paired(next(iter(first)), next(iter(second)))
                source[op] = 'alignment, paired: first=%s second=%s (%d pairs)' % (
                    mapping[op][0], mapping[op][1], first.most_common(1)[0][1])
            else:
                conflicts[op] = dict(c)
    for op, c in fused_votes.items():
        if op in mapping or op in conflicts:
            continue
        (a, b, split), n = c.most_common(1)[0]
        if len(c) == 1 and n >= 10:
            mapping[op] = Fused(a, b, split)
            source[op] = 'fused: %s + %s, arguments packed %s (%d aligned occurrences)' % (a, b, split, n)
    # aliases (two opcodes for one instruction) are allowed when each has unanimous votes
    dup = collections.Counter(n for n in mapping.values() if isinstance(n, str))
    for op in [o for o, n in mapping.items() if isinstance(n, str) and dup[n] > 1]:
        if votes[op][mapping[op]] < 10:
            conflicts[op] = {mapping.pop(op): 'duplicate name, too few votes'}; source.pop(op)
        else:
            source[op] += '; alias'
    log('alignment: %d bundled stdlib functions matched, %d opcodes, %d conflicting' % (aligned, len(mapping), len(conflicts)))

    used = collections.Counter(); where = collections.defaultdict(set); args = collections.defaultdict(set)
    for m in mods.values():
        for _, c in m.code.walk():
            for _, op, a in instructions(c.code):
                used[op] += 1; where[op].add(c.name); args[op].add(a)
    for comp, name in (('<dictcomp>', 'MAP_ADD'), ('<setcomp>', 'SET_ADD')):
        if name in [n for n in mapping.values() if isinstance(n, str)]:
            continue
        cand = [op for op in used if op not in mapping and op >= 90 and where[op] == {comp} and args[op] == {2}]
        if len(cand) == 1:
            mapping[cand[0]] = name; source[cand[0]] = 'only inside %s with argument 2' % comp

    known = json.load(open(os.path.join(DATA, 'known_maps.json')))
    for kname, k in sorted(known.items(), key=lambda kv: -len(kv[1]['mapping'])):
        km = {int(o): n for o, n in k['mapping'].items()}
        plain = {o: n for o, n in mapping.items() if isinstance(n, str)}
        agree = all(km.get(o, n) == n for o, n in plain.items())
        overlap = sum(1 for o in plain if o in km)
        if not agree or overlap < 40:
            continue
        filled = 0
        for o, n in km.items():
            if o not in mapping and n not in plain.values() and o in used:
                mapping[o] = n; source[o] = 'known map %s (agrees on all %d derived opcodes)' % (kname, overlap); filled += 1
        if filled:
            log('known map %s agrees on %d opcodes; filled %d' % (kname, overlap, filled))
    unmapped = {op: used[op] for op in used if op not in mapping}
    if unmapped:
        log('UNMAPPED opcodes (code objects using them are refused): %s' % sorted(unmapped.items(), key=lambda x: -x[1]))
    else:
        log('all %d opcodes used by this build are mapped' % len(used))
    return dict(mapping={o: mapping[o] for o in sorted(mapping)}, source=source, conflicts=conflicts,
                unmapped=unmapped, used=len(used), aligned_functions=aligned)
