"""Decompile, verify and assemble: the library entry points.

    from npkscript import pipeline
    pipeline.decompile_npk('script.npk', 'OUT', pipeline.Options(uncompyle2='u2/uncompyle2-1.1'))
    # or, from an extractor that already decrypts NXPK entries:
    pipeline.decompile_entries(((key, marshal_bytes) for ...), 'OUT', options, label='script.npk')

Stage 1 decompiles every module with uncompyle6 (dead-jump variant) and, with Python 2.7, verifies each function by
recompiling it. Stage 2 adds uncompyle6 on the unpatched bytecode and uncompyle2 for modules with an unverified
function or awkward `continue`/`else` shapes; each function is then taken from the most readable candidate whose
bytecode equals the original (helpers27/select27.py).
"""
import collections
import concurrent.futures as cf
import dataclasses
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
from typing import List, Optional

from . import decompilers, marshal27, opmap, rewrite

HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'helpers27')
AWKWARD = (re.compile(r'^(\s+)pass\n\s*else:', re.M),
           re.compile(r'^\s+(?:return|raise|continue|break)\b[^\n]*\n\s*else:\s*$', re.M))
HEADER_NOISE = re.compile(r'#\s*(uncompyle6 version|Python bytecode version|Decompiled from:|Embedded file name:|'
                          r'okay decompiling)')


@dataclasses.dataclass
class Options:
    python2: Optional[str] = shutil.which('python2.7') or shutil.which('python2')
    uncompyle2: Optional[str] = None            # uncompyle2 1.1 source dir (contains uncompyle2/)
    only: List[str] = dataclasses.field(default_factory=list)
    include_stdlib: bool = False
    align_with: List[str] = dataclasses.field(default_factory=list)   # script.npk paths of related builds
    extra: List[str] = dataclasses.field(default_factory=list)        # other decompiles of the same build
    jobs: int = os.cpu_count() or 4
    timeout: int = 300
    max_size: int = 0
    keep_work: bool = False
    log: object = print


def decompile_npk(npk, out, opts):
    sha = hashlib.sha256(open(npk, 'rb').read()).hexdigest()
    return decompile_modules(marshal27.load(npk), out, opts, label=os.path.basename(npk), archive_sha256=sha,
                             archive_path=npk)


def decompile_entries(pairs, out, opts, label='script.npk', archive_sha256=None):
    """For extractors that already decrypt archive entries: (entry key, marshal bytes) pairs."""
    return decompile_modules(marshal27.load_entries(pairs), out, opts, label=label, archive_sha256=archive_sha256)


def decompile_modules(mods, out, opts, label='script.npk', archive_sha256=None, archive_path=None):
    if os.path.exists(out):
        raise FileExistsError(out)
    missing = decompilers.available()
    if missing:
        raise RuntimeError(missing)
    os.makedirs(out)
    work = os.path.join(out, '_work')
    src_root = os.path.join(out, 'source')
    log_lines = []

    def log(*x):
        s = ' '.join(str(y) for y in x)
        opts.log(s)
        log_lines.append(s)

    log('%s: %d modules' % (label, len(mods)) + (', sha256 %s' % archive_sha256 if archive_sha256 else ''))
    others = []
    for o in opts.align_with:
        om_ = marshal27.load(o)
        r = opmap.derive(om_, log=lambda *x: None)
        if r['unmapped']:
            log('note: %s itself has unmapped opcodes; only its mapped ones are used' % o)
        others.append((om_, r['mapping']))
    om = opmap.derive(mods, log=log, align_with=others)
    mapping = om['mapping']
    json.dump(dict(archive=archive_path or label, archive_sha256=archive_sha256, align_with=opts.align_with,
                   mapping={str(k): (list(v) if isinstance(v, tuple) else v) for k, v in mapping.items()},
                   source={str(k): v for k, v in om['source'].items()},
                   conflicts={str(k): v for k, v in om['conflicts'].items()},
                   unmapped={str(k): v for k, v in om['unmapped'].items()}),
              open(os.path.join(out, 'opmap.json'), 'w'), indent=1)

    chosen = sorted(p for p in mods if (opts.include_stdlib or not p.startswith('lib/'))
                    and (not opts.only or any(s.lower() in p.lower() for s in opts.only)))
    large = [p for p in chosen if opts.max_size and len(mods[p].data) > opts.max_size]
    chosen = [p for p in chosen if p not in large]
    if large:
        log('%d modules over %d bytes skipped (max_size): %s' % (len(large), opts.max_size, ', '.join(large)))
    refused = {}
    todo = []
    for p in chosen:
        ops = rewrite.unmapped(mods[p], mapping)
        if ops:
            refused[p] = 'unmapped opcodes %s' % ops
            continue
        try:
            pycs = {kind: rewrite.to_pyc(mods[p], mapping, kind == 'patched') for kind in ('raw', 'patched')}
        except rewrite.Unsupported as e:
            refused[p] = 'cannot rewrite exactly: %s' % e
            continue
        for kind, b in pycs.items():
            dst = os.path.join(work, kind, p + 'c')
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, 'wb').write(b)
        todo.append(p)
    log('%d modules selected, %d refused' % (len(chosen), len(refused)))

    py2 = opts.python2
    use_u2 = bool(py2 and opts.uncompyle2)
    extra = [('extra%d' % i, decompilers.index_extra(r)) for i, r in enumerate(opts.extra)]

    def job(name, kind, p):
        pyc = os.path.join(work, kind, p + 'c')
        dst = os.path.join(work, name, p)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if name.startswith('u6'):
            return decompilers.uncompyle6(pyc, dst, opts.timeout)
        return decompilers.uncompyle2(py2, opts.uncompyle2, pyc, dst, opts.timeout)

    def decompile(cands, mods_):
        log('decompiling %d modules with %s (%d jobs)' % (len(mods_), ', '.join(n for n, _ in cands), opts.jobs))
        with cf.ThreadPoolExecutor(opts.jobs) as ex:
            futs = [ex.submit(job, n, k, p) for n, k in cands for p in mods_]
            for i, f in enumerate(cf.as_completed(futs), 1):
                f.result()
                if i % 500 == 0:
                    log('  %d/%d' % (i, len(futs)))

    def select(mods_, names):
        out_ = []
        chunks = [c for c in (mods_[i::opts.jobs] for i in range(opts.jobs)) if c]

        def one(i, chunk):
            lst = os.path.join(work, 'chunk%d.txt' % i)
            open(lst, 'w').write('\n'.join(chunk))
            rep = os.path.join(work, 'chunk%d.jsonl' % i)
            if os.path.exists(rep):
                os.remove(rep)
            subprocess.run([py2, '-E', '-s', '-B', os.path.join(HELPERS, 'select27.py'), os.path.join(work, 'raw'),
                            src_root, rep, lst] + ['%s=%s' % (n, os.path.join(work, n)) for n in names],
                           check=False, timeout=max(600, opts.timeout * len(chunk)))
            return [json.loads(l) for l in open(rep)] if os.path.exists(rep) else []
        with cf.ThreadPoolExecutor(opts.jobs) as ex:
            for r in ex.map(lambda ic: one(*ic), enumerate(chunks)):
                out_ += r
        return out_

    reports = []
    names = ['u6']
    decompile([('u6', 'patched')], todo)
    if py2:
        log('stage 1: verifying every function by recompilation')
        first = {r['module']: r for r in select(todo, names) if 'module' in r}

        def awkward(p):
            t = os.path.join(src_root, p)
            if not os.path.exists(t):
                return False
            text = open(t, encoding='utf-8', errors='replace').read()
            return any(rx.search(text) for rx in AWKWARD)
        retry = [p for p in todo if p not in first or not first[p].get('compiles')
                 or first[p].get('verified') != first[p].get('functions') or awkward(p)]
        log('stage 1: %d of %d modules fully verified and flat; %d go to stage 2'
            % (len(todo) - len(retry), len(todo), len(retry)))
        reports = [first[p] for p in todo if p in first and p not in retry]
        if retry:
            cands2 = [('u6raw', 'raw')] + ([('u2', 'raw')] if use_u2 else [])
            decompile(cands2, retry)
            for name, idx in extra:
                for p in retry:
                    if p in idx:
                        dst = os.path.join(work, name, p)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copyfile(idx[p], dst)
            names = ['u6'] + [n for n, _ in cands2] + [n for n, _ in extra]
            log('stage 2: verifying with %s' % ', '.join(names))
            reports += select(retry, names)
    else:
        log('no Python 2.7: functions are not verified; using uncompyle6 output (dead-jump variant)')
        for p in todo:
            dst = os.path.join(src_root, p)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(work, 'u6', p), dst)
            reports.append(dict(module=p, functions=None, verified=None))

    rep_by = {r['module']: r for r in reports if 'module' in r}
    for p in todo:
        dst = os.path.join(src_root, p)
        if not os.path.exists(dst):
            continue
        r = rep_by.get(p, {})
        body = open(dst, encoding='utf-8', errors='replace').read()
        body = '\n'.join(l for l in body.split('\n') if not HEADER_NOISE.match(l)).lstrip('\n')
        ver = ('%d of %d functions verified by recompilation' % (r['verified'], r['functions'])
               if r.get('functions') is not None else 'not verified (no Python 2.7)')
        head = ('# %s\n# Decompiled by npk_decompile from %s%s; module sha256 %s...\n# %s\n\n'
                % (p, label, ' (sha256 %s...)' % archive_sha256[:16] if archive_sha256 else '',
                   mods[p].sha256[:16], ver))
        open(dst, 'w', encoding='utf-8').write(head + body.rstrip() + '\n')
    with open(os.path.join(out, 'verification.jsonl'), 'w') as f:
        for r in reports:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    tot = sum(r.get('functions') or 0 for r in reports)
    ver = sum(r.get('verified') or 0 for r in reports)
    comp = sum(1 for r in reports if r.get('compiles'))
    picks = collections.Counter(d['chosen'] for r in reports for d in (r.get('detail') or {}).values())
    fused = sorted(str(k) for k, v in mapping.items() if isinstance(v, tuple) and len(v) == 3)
    lines = ['# npk_decompile report', '',
             '- Archive: `%s`' % (archive_path or label) + (', sha256 `%s`' % archive_sha256 if archive_sha256 else ''),
             '- Date: %s' % datetime.date.today().isoformat(),
             '- Modules: %d in the archive, %d selected, %d decompiled, %d refused'
             % (len(mods), len(chosen), len(todo), len(refused)),
             '- Opcode map: %d opcodes mapped (%s), %d unmapped (`opmap.json` gives the evidence for each)'
             % (len(mapping), 'fused: %s' % ', '.join(fused) if fused else 'no fused instructions',
                len(om['unmapped'])),
             '- Candidates: %s (stage 2 only where uncompyle6 alone did not verify or read flat)' % ', '.join(names),
             '- Skipped as larger than %d bytes: %s' % (opts.max_size, ', '.join('`%s`' % p for p in large) or 'none')]
    if py2:
        lines += ['- Functions verified by recompilation: **%d of %d** (%.1f%%)' % (ver, tot, 100.0 * ver / max(tot, 1)),
                  '- Modules whose assembled source compiles as Python 2.7: %d of %d' % (comp, len(todo)),
                  '- Verified functions taken from: %s'
                  % ', '.join('%s %d' % (k, v) for k, v in picks.most_common() if k)]
    lines += ['', '## Refused modules', ''] + ['- `%s`: %s' % (p, why) for p, why in sorted(refused.items())]
    if py2:
        lines += ['', '## Unverified functions', '',
                  'Their text is kept (marked `# [npk_decompile] UNVERIFIED` in the source) but recompiling it does not',
                  'give the original bytecode, so it may be wrong.', '']
        for r in sorted(reports, key=lambda r: r.get('module', '')):
            bad = [k for k, d in (r.get('detail') or {}).items() if not d['chosen']]
            if bad:
                lines.append('- `%s`: %s' % (r['module'], ', '.join('`%s`' % b for b in bad)))
    lines += ['', '## Log', '', '```'] + log_lines + ['```', '']
    open(os.path.join(out, 'REPORT.md'), 'w').write('\n'.join(lines))
    if not opts.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    log('done: %s' % os.path.join(out, 'REPORT.md'))
    return dict(modules=len(mods), decompiled=len(todo), refused=refused, functions=tot, verified=ver)
