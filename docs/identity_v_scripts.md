# Identity V 2017-2018 scripts (`script.npk`)

The 2017-2018 Identity V betas (NeoX engine) ship their game logic as Python 2.7 bytecode in `script.npk`.
NeoXtractor's NPK reader already decrypts these entries (ROTOR + zlib, `core/npk/decompression.py`). Each entry is
then a standard Python 2.7 `marshal` stream of one module's code object, except that **NetEase renumbered the
opcodes**. `id5mapping/scripts/` recovers the numbering and decompiles the modules. The steps and supported builds
are below.

Everything is static: game code is read, never imported or executed. Decompilers read it, and Python's
`compile()` checks their output.

## Use

**Tools > ID5 Script Decompile...**, with the **Identity V** config active and a `script.npk` opened. From a source
checkout:

```bash
python -m id5mapping.scripts path/to/script.npk OUT_DIR [--uncompyle2 DIR] [--align-with DEC2017_SCRIPT_NPK]
```

| Option | Effect |
|---|---|
| Python 2.7 (`--python2`, default: found on `PATH`) | verifies every function by recompiling it (strongly recommended) |
| uncompyle2 (`--uncompyle2`) | the uncompyle2 1.1 source folder, a second decompiler. It isn't bundled; fetch it with `pip download --no-deps --no-binary :all: uncompyle2==1.1` and unpack |
| Align with (`--align-with`) | a December 2017 `script.npk`, needed for Feb-May 2018 builds |
| `--only`, `--include-stdlib`, `--max-size`, `--keep-work` | restrict modules, add the bundled `lib/`, skip huge modules, keep intermediates |

Output in `OUT_DIR`:
- `source/` holds one `.py` per module in the game's own folder layout. A header gives the hashes and how many
  functions were verified.
- `REPORT.md` has the counts, refused modules and every unverified function. In the source, those functions are
  marked `# [NeoXtractor] UNVERIFIED`.
- `opmap.json` holds the recovered numbering with the evidence for each opcode.
- `verification.jsonl` gives each candidate's verdict per function.

## How the numbering is recovered (`opmap.py`)

1. **Standard library alignment.** The client bundles its own Python 2.7 standard library under `lib/`. A
   bundled function whose names, local names, length and operands equal the same function compiled by CPython
   2.7.18 (`scripts/data/stdlib27_ref.json.gz`, built by `id5mapping/tools/build_stdlib27_ref.py`) pins every
   opcode in it. Split votes need at least 10 votes and a 5:1 majority, because a few functions changed between
   2.7 point releases.
2. **Alignment with a mapped build** (`--align-with`). The same comparison runs against game functions of a build
   whose numbering is known. A sequence diff of otherwise identical functions also finds opcodes whose
   instruction changed length.
3. **Structural rules:**
   - an unknown opcode found only in `<dictcomp>` (`<setcomp>`) code, always with argument 2, is `MAP_ADD`
     (`SET_ADD`);
   - an unknown argument-taking opcode is `EXTENDED_ARG` when it only ever sits right before a constant or name
     instruction whose combined index `(prefix << 16) | arg` lies beyond 65,535 and inside that code object's
     table. Huge data tables need it.
4. **Known map** (`scripts/data/opmap_known.json`). The complete Nov 2017 - Jan 2018 numbering fills gaps, but only
   when it agrees with everything found so far.

Engine tricks of the Feb-May 2018 builds:
- **aliases:** two numbers for one instruction;
- **paired opcodes:** one number appears twice in a row and stands for `LOAD_FAST` then `LOAD_ATTR`, told apart by
  position in the run;
- **fused opcodes:** opcode 173 is one 3-byte instruction standing for `LOAD_FAST hi` + `LOAD_CONST lo`.

Opcodes that stay unknown are reported, and modules using them are refused.

## Rewriting and decompiling (`rewrite.py`, `decompile.py`)

- **Plain code objects** are rewritten in place: only opcode bytes change, and the build's line tables are kept.
- **Code with fused opcodes** is reassembled. Each fused opcode is expanded, every jump is retargeted and the line
  table is remapped. Anything that cannot be relocated exactly is refused.
- **Stage 1** runs uncompyle6 (in worker processes) on bytecode where the unreachable `JUMP_FORWARD 0`, which the
  2017 compiler leaves after a `continue`, has been blanked.
- **Stage 2** covers modules with an unverified function or awkward `continue`/`else` shapes. It adds uncompyle6
  on the unmodified bytecode and uncompyle2.

## Verification (`py27/`, Python 2.7)

Every function of every candidate is cut out, compiled inside a stub of its class and compared with the original
bytecode (`py27/compare.py`). The comparison ignores only differences between the 2017-2018 compilers and
CPython 2.7.18 that do not change behaviour:
- unreachable code, NOPs and `JUMP_FORWARD 0`;
- constant folding, which the Feb-May 2018 compiler skips: `LOAD_CONST`×k + `BUILD_TUPLE k`, and a constant list
  before `in`;
- the order of closure cells.

Each module is built on the candidate that compiles and holds the most functions. Every function is then taken
from the most readable candidate that verified.

Two cosmetic clean-ups are applied before verification, so they are re-checked like everything else:
- dropping the bare `return` uncompyle6 prints at the end of a function;
- writing UTF-8 escapes in byte strings as the characters they encode.

Nothing is removed or redacted.

## Limits

- Decompiler processes run with a memory cap: 4 GB, then a larger cap on one retry, so one runaway module cannot
  exhaust the machine. The largest data tables can still exceed what the decompilers handle; Dec 2017's 2.6 MB
  `pydata/dis_anim_offset.py` is one. Such modules are listed under "Modules no decompiler could handle" in
  `REPORT.md`, never left silently empty. Rebuilding pure data tables directly from their constants would cover
  them, but that isn't implemented yet.
- Functions that do not verify are kept but marked; they may be wrong.

## Supported builds

| Build | Opcodes | Functions verified |
|---|---|---|
| Nov 2017 | complete | 99.8% |
| Dec 2017 (1.5.2) | complete | 99.9% |
| Jan 2018 (1.5.2 + patch) | complete | 99.9% |
| Mar 2018 (1.5.19), with `--align-with` Dec 2017 | complete (fused, paired, EXTENDED_ARG) | 99.4% |
| Apr 2018, with `--align-with` Dec 2017 | complete | 99.6% |
| May 2018, with `--align-with` Dec 2017 | complete | 99.6% |
| Feb 2018 third-party repack, with `--align-with` Dec 2017 | complete except opcode 1, which only the repacker's injected code uses | 94.7% (its patched modules cannot verify) |
