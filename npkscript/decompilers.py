"""Decompiler drivers (uncompyle6, uncompyle2) and clean-up of their raw output. Decompilers read .pyc files;
nothing they read is executed.

uncompyle6 runs in-process through its Python API, each module in a short-lived worker process so a hang can be
timed out and no `uncompyle6` executable is needed (works in a frozen PyInstaller build). uncompyle2 runs under an
external Python 2.7.
"""
import contextlib
import multiprocessing
import os
import re
import subprocess
import traceback

HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'helpers27')
DUMP_START = '-- Stacks of completed symbols:'
CODE_START = re.compile(r'^(import |from |class |def |@|if |try:|for |while |with |print|[A-Za-z_][\w.]*\s*(=|\(|\[))')


def run(cmd, out_path, timeout):
    """Run a decompiler with stdout to `out_path`; False on failure or timeout (the file is then empty or partial)."""
    try:
        with open(out_path, 'w', encoding='utf-8') as out:
            p = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        open(out_path, 'w').close()
        return False


def comment_dumps(text):
    """Turn uncompyle6 grammar/instruction dumps (printed for functions it cannot parse) into comments, and drop the
    bare `return` it prints for a module's implicit `return None`."""
    out, inside = [], False
    for line in text.split('\n'):
        if line.startswith(DUMP_START):
            inside = True
            out.append('# [npk_decompile] decompiler diagnostic for a function below:')
            continue
        if inside and CODE_START.match(line) and '::=' not in line:
            inside = False
        out.append('# ' + line if inside else line)
    for k in range(len(out) - 1, -1, -1):
        if out[k].strip() and not out[k].startswith('#'):
            if out[k] == 'return':
                del out[k]
            break
    return '\n'.join(out)


def _uncompyle6_worker(pyc, dst):
    from uncompyle6.main import decompile_file
    with open(dst, 'w', encoding='utf-8') as out, contextlib.redirect_stdout(out):
        try:
            decompile_file(pyc, out)
        except Exception:      # a module the decompiler cannot handle at all: keep its message as a comment
            out.write('\n# [npk_decompile] uncompyle6 failed:\n' +
                      ''.join('# ' + l + '\n' for l in traceback.format_exc().splitlines()[-3:]))


def uncompyle6(pyc, dst, timeout):
    """Decompile one .pyc into `dst` (comment-cleaned); False on timeout or crash."""
    proc = multiprocessing.Process(target=_uncompyle6_worker, args=(pyc, dst), daemon=True)
    proc.start()
    proc.join(timeout)
    ok = proc.exitcode == 0
    if proc.is_alive():
        proc.terminate()
        proc.join()
        open(dst, 'w').close()
    if os.path.exists(dst):
        t = open(dst, encoding='utf-8', errors='replace').read()
        open(dst, 'w', encoding='utf-8').write(comment_dumps(t))
    return ok


def available():
    """None when uncompyle6 can be imported, else the reason."""
    try:
        import uncompyle6.main  # noqa: F401
        return None
    except ImportError as e:
        return 'uncompyle6 is not installed (pip install uncompyle6): %s' % e


def uncompyle2(python2, source_dir, pyc, dst, timeout):
    return run([python2, '-E', '-s', '-B', os.path.join(HELPERS, 'u2run27.py'), source_dir, pyc, dst],
               dst + '.log', timeout)


def index_extra(root):
    """Map module paths to files of another decompile: by relative path, or by an '#Embedded file name:' header."""
    idx = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            if not f.endswith('.py'):
                continue
            with open(p, encoding='utf-8', errors='replace') as fh:
                first = fh.readline()
            m = re.match(r'#\s*Embedded file name:\s*(.*)', first)
            rel = m.group(1).strip().replace('\\', '/') if m else os.path.relpath(p, root).replace(os.sep, '/')
            if ':' in rel and 'script/' in rel:
                rel = rel.split('script/', 1)[1]
            idx.setdefault(rel, p)
    return idx
