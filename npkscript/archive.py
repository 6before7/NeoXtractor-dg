"""NXPK script archives of the 2017-2018 Identity V client family (NeoX engine), statically.

An NXPK archive is an index of 28-byte rows at `index_offset`. Each entry is zlib data; script entries are also
encrypted with Python 2's `rotor` cipher under NeoX's loader key and byte-transformed, unless the row's flag is 1.
This is the container layer: another extractor that already yields decrypted entries can skip it and pass
(key, bytes) pairs to `npkscript.pipeline.decompile_entries`.
"""
import math
import struct
import zlib

LIMIT = 64 * 1024 * 1024


def _cdiv(a, b):
    return (abs(a) // b) * (-1 if a < 0 else 1)


class Rotor:
    """Python 2's `rotor` module (6 rotors)"""

    def __init__(self, key, count=6):
        k = [995, 576, 767, 671, 463]
        for ch in key:
            r = [(x << 3 | x >> 13) for x in k]
            k = [(r[0] + ch) & 65535, (r[1] ^ ch) & 65535, (r[2] - ch) & 65535, (ch - r[3]) & 65535, (r[4] ^ ~ch) & 65535]
        k[1] |= 1
        self.seed = [x if x < 32768 else x - 65536 for x in k[:3]]
        self.positions, self.advances, self.inverse = [], [], []
        for _ in range(count):
            self.positions.append(self._rand(256))
            self.advances.append(1 + 2 * self._rand(128))
            e, d = list(range(256)), list(range(256))
            for size in range(256, 1, -1):
                q = self._rand(size)
                j = e[q]
                e[q], e[size - 1] = e[size - 1], j
                d[j] = size - 1
            d[e[0]] = 0
            self.inverse.append(d)

    def _rand(self, size):
        for i, (mul, div, sub, mod) in enumerate(((171, 177, 2, 30269), (172, 176, 35, 30307), (170, 178, 63, 30323))):
            x = self.seed[i]
            q = _cdiv(x, div)
            x = mul * (x - q * div) - sub * q
            self.seed[i] = x + mod if x < 0 else x
        t = self.seed[0] / 30269.0 + self.seed[1] / 30307.0 + self.seed[2] / 30323.0
        v = t - math.floor(t)
        return int((0.0 if v >= 1 else v) * size) % size

    def decrypt(self, data):
        pos = self.positions[:]
        n = len(pos)
        order = range(n - 1, -1, -1)
        out = bytearray(len(data))
        for off, ch in enumerate(data):
            for i in order:
                ch = pos[i] ^ self.inverse[i][ch]
            out[off] = ch
            for i in range(n):
                t = pos[i] + self.advances[i]
                pos[i] = t % 256
                if t >= 256 and i + 1 < n:
                    pos[i + 1] = (pos[i + 1] + 1) % 256
        return bytes(out)


def loader_key():
    a, b, c = b'j2h56ogodh3se', b'=dziaq.', b'|os=5v7!"-234'
    return a * 4 + (b + a + c) * 5 + b'!' + b'#' + b * 7 + c * 2 + b'*' + b'&' + b"'"


def _inflate(raw):
    d = zlib.decompressobj()
    out = d.decompress(raw, LIMIT + 1)
    if len(out) > LIMIT or not d.eof or d.unused_data or d.unconsumed_tail:
        raise ValueError('incomplete, trailing or oversized zlib data')
    return out


def _untransform(raw):
    return (bytes(ch ^ 154 for ch in raw[:128]) + raw[128:])[::-1]


def entries(path):
    """Yield (entry_key, decrypted_marshal_bytes) for every entry of an NXPK script archive."""
    data = open(path, 'rb').read()
    magic, count, _, _, _, index = struct.unpack_from('<4s5I', data, 0)
    if magic != b'NXPK':
        raise ValueError('not an NXPK archive')
    rot = Rotor(loader_key())
    for i in range(count):
        key, off, size, _, _, _, flag = struct.unpack_from('<7I', data, index + 28 * i)
        blob = data[off:off + size]
        yield key, (_inflate(blob) if flag == 1 else _untransform(_inflate(rot.decrypt(blob))))
