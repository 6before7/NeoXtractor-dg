"""Python 2.7 marshal reading that records where each code object's co_code and line table lie, so they can be
rewritten in place or relocated (see rewrite.py). Never imports or executes the code it reads."""
import hashlib
import struct

from .archive import entries


class Code:
    __slots__ = ('argcount', 'flags', 'code_at', 'code', 'consts', 'names', 'varnames', 'filename', 'name',
                 'lnotab_at', 'lnotab')

    def children(self):
        return [c for c in self.consts if isinstance(c, Code)]

    def walk(self, path=''):
        p = path + '/' + self.name
        yield p, self
        for c in self.children():
            yield from c.walk(p)


class _Sentinel:
    def __init__(self, n):
        self.n = n

    def __repr__(self):
        return self.n


ELLIPSIS, STOPITER, NULL = _Sentinel('Ellipsis'), _Sentinel('StopIteration'), _Sentinel('<null>')


class Reader:
    """Python 2.7 marshal reader that records where each code object's co_code and line table lie
    (`Code.code_at`, `Code.lnotab_at`: offsets of their bytes, each preceded by a 4-byte length)."""

    def __init__(self, data):
        self.d = data
        self.p = 0
        self.refs = []

    def _n(self, fmt, size):
        v = struct.unpack_from(fmt, self.d, self.p)[0]
        self.p += size
        return v

    def _text(self):
        n = self._n('<i', 4)
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def load(self):
        t = chr(self.d[self.p])
        self.p += 1
        if t == '0':
            return NULL
        if t == 'N':
            return None
        if t in 'FT':
            return t == 'T'
        if t == 'S':
            return STOPITER
        if t == '.':
            return ELLIPSIS
        if t == 'i':
            return self._n('<i', 4)
        if t == 'I':
            return self._n('<q', 8)
        if t == 'f':
            n = self.d[self.p]
            self.p += 1
            v = float(self.d[self.p:self.p + n].decode('ascii'))
            self.p += n
            return v
        if t == 'g':
            return self._n('<d', 8)
        if t == 'x':
            parts = []
            for _ in range(2):
                n = self.d[self.p]
                self.p += 1
                parts.append(float(self.d[self.p:self.p + n].decode('ascii')))
                self.p += n
            return complex(*parts)
        if t == 'y':
            r, i = struct.unpack_from('<dd', self.d, self.p)
            self.p += 16
            return complex(r, i)
        if t == 'l':
            n = self._n('<i', 4)
            v = 0
            for j in range(abs(n)):
                v += self._n('<h', 2) << (15 * j)
            return -v if n < 0 else v
        if t == 's':
            return self._text()
        if t == 't':
            v = self._text()
            self.refs.append(v)
            return v
        if t == 'R':
            return self.refs[self._n('<i', 4)]
        if t == 'u':
            return self._text().decode('utf-8', 'replace')
        if t in '([':
            n = self._n('<i', 4)
            v = [self.load() for _ in range(n)]
            return tuple(v) if t == '(' else v
        if t == '{':
            d = {}
            while True:
                k = self.load()
                if k is NULL:
                    return d
                d[k] = self.load()
        if t in '<>':
            n = self._n('<i', 4)
            return frozenset(self.load() for _ in range(n))
        if t == 'c':
            c = Code()
            c.argcount = self._n('<i', 4)
            self._n('<i', 4)
            self._n('<i', 4)
            c.flags = self._n('<i', 4)
            if chr(self.d[self.p]) != 's':
                raise ValueError('co_code is not a plain string')
            self.p += 1
            n = self._n('<i', 4)
            c.code_at = self.p
            c.code = self.d[self.p:self.p + n]
            self.p += n
            c.consts = self.load()
            c.names = [_name(x) for x in self.load()]
            c.varnames = [_name(x) for x in self.load()]
            self.load()
            self.load()
            c.filename = _name(self.load())
            c.name = _name(self.load())
            self._n('<i', 4)
            c.lnotab_at = None
            if chr(self.d[self.p]) in 'st':            # plain or interned string: its bytes can be rewritten
                c.lnotab_at = self.p + 5
            c.lnotab = self.load()
            if not isinstance(c.lnotab, bytes):
                c.lnotab, c.lnotab_at = b'', None
            return c
        raise ValueError('unknown marshal type %r at %d' % (t, self.p - 1))


def _name(x):
    return x.decode('utf-8', 'replace') if isinstance(x, bytes) else str(x)


class Module:
    def __init__(self, key, data):
        self.key = key
        self.data = data
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.code = Reader(data).load()
        if not isinstance(self.code, Code):
            raise ValueError('entry is not a code object')
        fn = self.code.filename
        for sep in ('script\\', 'script/'):           # PC builds record the build machine's absolute path
            if ':' in fn and sep in fn:
                fn = fn.split(sep, 1)[1]
        self.path = fn.replace('\\', '/')


def load_entries(pairs):
    """{module path ('a/b.py'): Module} from (entry key, decrypted marshal bytes) pairs; entries that are not code
    objects (resources, data) are skipped."""
    mods = {}
    for key, data in pairs:
        try:
            m = Module(key, data)
        except (ValueError, struct.error, IndexError):
            continue
        mods[m.path] = m
    return mods


def load(path):
    """Modules of an NXPK script archive (see archive.entries)."""
    return load_entries(entries(path))
