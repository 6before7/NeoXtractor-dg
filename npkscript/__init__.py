"""npkscript: static decompilation of 2017-2018 Identity V (NeoX engine) script archives.

Layers: archive (NXPK entries) -> marshal27 (code objects with offsets) -> opmap (opcode recovery) -> rewrite
(.pyc in CPython 2.7 numbering, fused instructions expanded) -> decompilers -> pipeline (verification by
recompilation, assembly, report). Game code is never imported or executed.
"""
