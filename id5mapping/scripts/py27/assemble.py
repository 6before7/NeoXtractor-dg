# -*- coding: utf-8 -*-
"""Verify decompiled functions by recompiling them, and assemble each module from verified text (Python 2.7).

For every function of the original module, each candidate decompile's text of that function is compiled inside a
stub of its class (so private-name mangling is right) and compared with the original bytecode (``compare.py``). The
module is then built on the candidate that compiles and holds the most functions, and every function is swapped
for the most readable candidate text that verified. Nothing is executed: source is only compiled.

Usage (called by ``id5mapping/scripts/decompile.py``):
    python2.7 assemble.py <pyc root> <output root> <report.jsonl> <module list file> NAME=DIR [NAME=DIR ...]
"""

import json
import marshal
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare  # noqa: E402  (a sibling module; this file runs as a script)
import source  # noqa: E402

UNVERIFIED_NOTE = ('# [NeoXtractor] UNVERIFIED: recompiling this function does not reproduce the original '
                   'bytecode; read with care')


def _compiles(text, filename):
    try:
        compile(text, filename, 'exec')
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def verify_function(lines, key, original, filename):
    """'same', 'differs', 'missing' or 'nocompile' for one candidate's text of one function."""
    classes, name = key[:-2], key[-2]
    stub = ['    ' * i + 'class %s:' % c for i, c in enumerate(classes)] + lines
    try:
        code = compile('\n'.join(stub) + '\n', filename, 'exec')
    except Exception:  # pylint: disable=broad-except
        return 'nocompile'
    for _ in classes:
        code = [c for c in code.co_consts if isinstance(c, types.CodeType)][0]
    defined = [c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name]
    if not defined:
        return 'missing'
    return 'same' if compare.same_function(original, defined[-1]) else 'differs'


def _candidates(paths):
    """[(name, text lines, function blocks)] for the candidate files that exist."""
    found = []
    for name, path in paths:
        if os.path.exists(path):
            text = source.tidy(open(path).read())
            found.append((name, text.split('\n'), source.function_blocks(text)))
    return found


def _verdicts(functions, candidates, filename):
    verdicts = {}
    for key, original in functions.items():
        verdicts[key] = {}
        for name, lines, blocks in candidates:
            if key not in blocks:
                verdicts[key][name] = 'missing'
            else:
                text = source.block_lines(lines, blocks[key], 4 * (len(key) - 2))
                verdicts[key][name] = verify_function(text, key, original, filename)
    return verdicts


def _pick_base(functions, candidates, filename):
    """The candidate that compiles and holds the most functions (so every verified function has a place to go),
    is not empty (a failed decompile), then the most readable by priority order."""
    order = []
    for priority, (name, lines, blocks) in enumerate(candidates):
        present = sum(1 for key in functions if key in blocks)
        empty = not any(line.strip() and not line.lstrip().startswith('#') for line in lines)
        order.append((not _compiles('\n'.join(lines), filename), -present, empty, priority))
    return candidates[min(order)[-1]]


def _best_text(key, verdicts, candidates, base_name, indent):
    """(candidate name, re-indented text) of the most readable verified version of a function."""
    scored = []
    for priority, (name, lines, blocks) in enumerate(c for c in candidates if verdicts[key].get(c[0]) == 'same'):
        text = source.block_lines(lines, blocks[key], indent)
        scored.append((source.readability_penalty(text), name != base_name, priority, name, text))
    return min(scored)[3:] if scored else (None, None)


def _mark_unverified(text, functions, chosen):
    lines, blocks = text.split('\n'), source.function_blocks(text)
    for key in sorted((k for k in functions if not chosen.get(k) and k in blocks), key=lambda k: -blocks[k][0]):
        first, _, indent = blocks[key]
        lines.insert(first - 1, ' ' * indent + UNVERIFIED_NOTE)
    return '\n'.join(lines)


def assemble(pyc_path, candidate_paths):
    """(assembled source, report) for one module."""
    module = marshal.loads(open(pyc_path, 'rb').read()[8:])
    filename = module.co_filename
    functions = compare.functions_of(module)
    candidates = _candidates(candidate_paths)
    verdicts = _verdicts(functions, candidates, filename)
    base_name, result, base_blocks = _pick_base(functions, candidates, filename)
    result = list(result)
    chosen, swaps = {}, []
    for key in functions:
        base_ok = verdicts[key].get(base_name) == 'same'
        if key not in base_blocks:
            chosen[key] = base_name if base_ok else None
            continue
        name, text = _best_text(key, verdicts, candidates, base_name, base_blocks[key][2])
        if name in (None, base_name):
            chosen[key] = name
        else:
            swaps.append((base_blocks[key], text, key, name))
    for (first, last, _), text, key, name in sorted(swaps, key=lambda s: s[0][0], reverse=True):  # bottom up
        trial = result[:first - 1] + text + [''] + result[last:]
        if _compiles('\n'.join(trial), filename) or not _compiles('\n'.join(result), filename):
            result, chosen[key] = trial, name
        else:
            chosen[key] = base_name if verdicts[key].get(base_name) == 'same' else None
    final = _mark_unverified('\n'.join(result), functions, chosen)
    detail = {}
    for key in functions:
        label = '/'.join(key[:-1]) + ('' if key[-1] == 0 else '#%d' % key[-1])
        detail[label] = {'verdict': verdicts[key], 'chosen': chosen.get(key)}
    report = {'base': base_name, 'compiles': _compiles(final, filename), 'functions': len(functions),
              'verified': sum(1 for k in functions if chosen.get(k)), 'detail': detail}
    return final, report


def main(argv):
    pyc_root, out_root, report_path, module_list = argv[:4]
    roots = [arg.split('=', 1) for arg in argv[4:]]
    with open(report_path, 'w') as report_file:
        for module in open(module_list).read().split():
            try:
                text, report = assemble(os.path.join(pyc_root, module + 'c'),
                                        [(name, os.path.join(root, module)) for name, root in roots])
            except Exception as e:  # pylint: disable=broad-except
                report_file.write(json.dumps({'module': module, 'error': repr(e)[:300]}) + '\n')
                continue
            target = os.path.join(out_root, module)
            if not os.path.isdir(os.path.dirname(target)):
                os.makedirs(os.path.dirname(target))
            open(target, 'w').write(text)
            report['module'] = module
            report_file.write(json.dumps(report) + '\n')


if __name__ == '__main__':
    main(sys.argv[1:])
