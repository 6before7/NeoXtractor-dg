# Python 2.7: compile (never run) the local CPython 2.7 standard library and write the opcode-alignment reference
# used by id5mapping/scripts/opmap.py: {relative module path: [[code path, names, varnames, co_code hex], ...]} as gzipped JSON.
# Usage: python2.7 -E -s id5mapping/tools/build_stdlib27_ref.py /usr/lib64/python2.7 id5mapping/scripts/data/stdlib27_ref.json.gz
import sys, os, json, types, gzip, binascii, platform
root, out = sys.argv[1:3]; ref = {}
def walk(c, p=''):
    p = p + '/' + c.co_name
    yield [p, list(c.co_names), list(c.co_varnames), binascii.hexlify(c.co_code)]
    for x in c.co_consts:
        if isinstance(x, types.CodeType):
            for y in walk(x, p): yield y
for d, dirs, fs in os.walk(root):
    dirs[:] = [x for x in dirs if x not in ('test', 'tests', 'site-packages', 'lib-dynload', 'idlelib', 'lib-tk', 'lib2to3')]
    for f in fs:
        if f.endswith('.py'):
            p = os.path.join(d, f)
            try:
                ref[os.path.relpath(p, root).replace(os.sep, '/')] = list(walk(compile(open(p).read(), p, 'exec')))
            except Exception:
                pass
meta = {'python': platform.python_version(), 'source': root, 'modules': len(ref)}
g = gzip.open(out, 'wb'); g.write(json.dumps({'meta': meta, 'modules': ref})); g.close()
print meta
