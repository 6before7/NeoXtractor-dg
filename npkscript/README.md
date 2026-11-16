# npkscript

Turns an Identity V `script.npk` from the 2017–2018 betas (NeoX engine, Python 2.7) into readable Python source.
It checks its own work: every function is recompiled and compared with the game's original bytecode, and anything
that doesn't match is marked in the output.

The process is entirely static. Game code is decrypted and read but never imported or executed. The only things run
on it are the decompilers and Python's `compile()`.

## What you need
- Nothing extra in a NeoXtractor release build: it bundles npkscript and uncompyle6 (3.9.3). From source,
  `uv sync` installs uncompyle6.
- **Python 2.7** (`python2.7` on your PATH), strongly recommended. Without it nothing is verified, and the second
  decompiler can't run. It isn't in most current distributions:
  - Fedora: `sudo dnf install python2.7`
  - macOS/Linux: `pyenv install 2.7.18`
  - Windows: the official 2.7.18 installer from python.org

  npkscript only uses it to *compile* code (never to run game code), so an old interpreter is safe here.
- **uncompyle2 1.1** (optional, recommended). It is pure Python and runs under Python 2.7, and it gets some functions
  right that uncompyle6 doesn't. It is not bundled; fetch the source like this:
  ```
  mkdir u2 && cd u2
  python3 -m pip download --no-deps --no-binary :all: uncompyle2==1.1
  tar -xzf uncompyle2-1.1.tar.gz          # then pass --uncompyle2 u2/uncompyle2-1.1
  ```

## Use
In NeoXtractor, open the `script.npk`, wait for it to load, then choose **Tools > Decompile Python Scripts...**.

From the command line:
```
neoxtractor decompile path/to/script.npk OUT_DIR --uncompyle2 u2/uncompyle2-1.1
python -m npkscript path/to/script.npk OUT_DIR ...       # same, from a source checkout
```
| Option | Effect |
|---|---|
| `--only KeyMgr ui/UIHall` | only modules whose path contains one of these strings (fast for a first look) |
| `--include-stdlib` | also decompile the bundled `lib/` (the stock Python library; skipped by default) |
| `--align-with other/script.npk` | another build of the same engine generation, used to map rare opcodes (see below) |
| `--extra DIR` | another decompile of the **same** build, used as one more candidate per function. Files are matched by path or by an `#Embedded file name:` first line |
| `--jobs N`, `--timeout S` | parallelism (default: all cores) and seconds per module per decompiler (default 300) |
| `--max-size BYTES` | quick look: skip modules with more bytecode than this (e.g. `250000` skips the few huge data tables and UI bindings that take minutes each). Default: decompile everything |
| `--keep-work` | keep the intermediate `.pyc` files and every candidate's output in `OUT_DIR/_work` |

A whole build of about 1,100 modules takes about 4 minutes on 16 cores (Dec 2017: 247 s), most of it spent on four
huge data-table and UI-binding modules; `--max-size 250000` skips those for a 30-second first look.

## Output
- `source/…`: one `.py` per module, in the game's own folder layout. Each starts with a header giving the module
  name, the archive and module hashes, and how many of its functions were verified.
- `REPORT.md`: counts, the opcode-map summary, refused modules, every unverified function and the run log.
- `opmap.json`: the recovered opcode map, with the evidence behind each opcode.
- `verification.jsonl`: per module and per function, each candidate's verdict and which one was used.

Inside the source, `# [npk_decompile] UNVERIFIED` sits above any function whose text, recompiled, doesn't give the
original bytecode. That function may be wrong, so read it with care. A function that no decompiler could handle
appears with the decompiler's diagnostic as comments.

## How it works
1. **Decrypt.** In NeoXtractor, its own NPK reader decrypts the entries. Standalone, `archive.py` does the
   same: NeoX's rotor cipher and zlib, byte for byte identical to NeoXtractor's reader. Each entry is a standard
   Python 2.7 marshal stream of the module's code object (`marshal27.py`).
2. **Recover the opcode shuffle** (`opmap.py`). NeoX renumbers Python's instructions. The client ships its own copy
   of the standard library, so every bundled function that equals the same function compiled by CPython 2.7.18
   (`data/cpython27_stdlib_ref.json.gz`) reveals which number means which instruction. The map is filled out by:
   - a rule for dictionary and set comprehensions;
   - optionally, alignment with an already-mapped build (`--align-with`);
   - known maps (`data/known_maps.json`), used only where they agree with everything derived so far.

   With `--align-with`, a sequence diff against an already-mapped build also pins opcodes the standard library
   never uses. It handles three engine tricks of the Feb–May 2018 builds:
   - **aliases:** two numbers for one instruction;
   - **paired opcodes:** one number that appears twice in a row and stands for, say, `LOAD_FAST` then `LOAD_ATTR`;
   - **fused opcodes:** one 3-byte instruction standing for two, with both arguments packed into its argument
     (opcode 173 = `LOAD_FAST hi` + `LOAD_CONST lo`).
3. **Rewrite** (`rewrite.py`).
   - **Plain code objects** are rewritten in place: only the opcode bytes change, and strings, names and the build's
     line-number tables stay exactly as shipped. The intermediate `.pyc` files (kept with `--keep-work`) carry the
     original line numbers; the decompiled text doesn't show them.
   - **Code objects with fused opcodes** are reassembled: each fused opcode is expanded into its two instructions,
     every jump is retargeted and the line table is remapped. Anything that can't be relocated exactly is refused,
     not approximated.
   - **A second variant** blanks out the unreachable `JUMP_FORWARD 0` that the 2017 compiler leaves after a
     `continue`, because it confuses uncompyle6.
4. **Decompile** in two stages. Stage 1 runs uncompyle6 (in-process, one worker process per module) (dead-jump variant) on every module and verifies it. Only
   modules with an unverified function, or with uncompyle6's awkward `continue`/`else` shapes, go on to stage 2,
   which adds uncompyle6 without the fix and uncompyle2.
5. **Verify and assemble** (Python 2.7, `helpers27/select27.py`). Every function in every candidate is cut out,
   compiled inside a stub of its class, and its bytecode compared with the original. The comparison ignores
   unreachable code, no-op jumps and the order of closure cells, all of which differ between the 2017 engine's compiler
   and CPython 2.7.18 without changing behaviour. The module starts from the most readable candidate, and each
   function is then taken from whichever verified candidate reads best (fewest `pass`/`else:` detours and `else:`
   after `return`), so structure follows the original source as closely as the decompilers allow.
6. **Tidy without changing bytecode.** Before verification every candidate gets two purely cosmetic fixes: the bare
   `return` uncompyle6 prints at the end of functions is dropped, and byte strings written as `'\xe8\xaf\xa5'` are
   written as the characters they encode (`'该'`). Both compile to identical bytecode, and the verifier re-checks
   them like everything else. Nothing is removed or redacted: the output is a 1:1 extraction of what the archive holds.

## Which builds work
| Build | Opcode map |
|---|---|
| Nov 2017, Dec 2017 (1.5.2), Jan 2018 | complete, on its own |
| Mar 2018 (1.5.19), Apr 2018, May 2018 | complete with `--align-with` a Dec 2017 `script.npk` (fused, paired and alias opcodes) |
| Feb 2018 `四测V3` (third-party repack) | complete except opcode 1, which only appears in modules the repacker edited; those are refused |
| Other builds | the tool tells you: unmapped opcodes are reported, and modules using them are refused, never guessed |

Each run's `REPORT.md` gives its verification rate.

## Use as a library (for other extractors)
The archive layer is separate from everything else, so a tool that already reads NPK archives can hand over
decrypted entries and skip `archive.py`:
```python
from npkscript import pipeline
opts = pipeline.Options(uncompyle2='u2/uncompyle2-1.1', align_with=['dec2017/script.npk'])
pipeline.decompile_npk('script.npk', 'OUT', opts)
# or, with entries an extractor already decrypted:
pipeline.decompile_entries(((key, marshal_bytes) for key, marshal_bytes in entries), 'OUT', opts, label='script.npk')
```
Lower layers can be used on their own:
- `marshal27.load_entries(pairs)`: code objects with byte offsets;
- `opmap.derive(modules, align_with=[...])`: the opcode map and its evidence;
- `rewrite.to_pyc(module, mapping)`: a CPython 2.7 `.pyc`.

## Layout
| Path | Role |
|---|---|
| `archive.py` | NXPK index and entry decryption, for standalone use (NeoXtractor uses its own reader) |
| `marshal27.py` | Python 2.7 marshal reader that records code and line-table offsets |
| `opcodes.py` | CPython 2.7 opcode table, instruction walking, `Fused`/`Paired` opcode kinds |
| `opmap.py` | opcode-map recovery |
| `rewrite.py` | in-place rewrite and fused-instruction reassembly into `.pyc` files |
| `decompilers.py` | uncompyle6 (in-process workers) and uncompyle2 drivers, output clean-up |
| `pipeline.py` | stages, verification, assembly, report; library entry points |
| `cli.py`, `__main__.py` | command line (`neoxtractor decompile`, `python -m npkscript`) |
| `helpers27/` | the Python 2.7 parts: `select27.py`, `verify27.py`, `u2run27.py` |
| `data/` | CPython 2.7.18 stdlib reference, known maps |

In NeoXtractor:
- `gui/windows/script_decompile_window.py`: the dialog;
- `hooks/hook-npkscript.py`, `hook-uncompyle6.py`, `hook-xdis.py`: PyInstaller;
- `tests/npkscript/`: unit tests (`python -m unittest discover -s tests/npkscript`);
- `tools/npkscript/build_stdlib_ref27.py`: rebuilds the stdlib reference.
