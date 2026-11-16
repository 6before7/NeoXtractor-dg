"""Recover the opcode numbering of a 2017-2018 NeoX ``script.npk``.

NetEase renumbered Python 2.7's opcodes per engine generation. The numbering is recovered from evidence in the
archive itself, never guessed:

1. **Standard library alignment.** The client ships its own copy of the Python 2.7 standard library (``lib/``).
   A bundled function whose names, local names, length and operands equal the same function compiled by CPython
   2.7.18 (``stdlib27_ref.json.gz``) pins every opcode in it.
2. **Alignment with a mapped build** (optional ``references``): the same, against game functions of a build whose
   numbering is already known. A sequence diff of otherwise identical functions also finds opcodes whose
   instruction changed length: *fused* opcodes, one instruction standing for two whose arguments it packs into
   its high and low byte (Feb-May 2018: ``LOAD_FAST`` + ``LOAD_CONST``).
3. **Structural rules.** An unknown opcode found only inside ``<dictcomp>`` (``<setcomp>``) code, always with
   argument 2, is ``MAP_ADD`` (``SET_ADD``). An unknown argument-taking opcode that only occurs in code objects
   with more than 65,535 constants or names, always right before a constant or name instruction whose combined
   index ``(prefix << 16) | arg`` falls inside that table, is ``EXTENDED_ARG`` (huge data tables need it).
4. **Known map** (``opmap_known.json``): a complete numbering of the same engine generation fills the rest, only if
   it agrees with everything found so far.

An opcode maps to a name, to ``("pair", first, second)`` (it appears twice in a row and stands for two different
instructions, Feb-May 2018) or to ``("fused", first, second, "hi/lo" | "lo/hi")``. Opcodes still unknown are
reported; ``rewrite.py`` refuses code that uses them.
"""

from __future__ import annotations

import difflib
import gzip
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from core.logger import get_logger

from .bytecode import CPY27, HAVE_ARGUMENT, CodeObject, ScriptModule, instructions

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
NAME_OF = {number: name for name, number in CPY27.items()}
MIN_VOTES = 10           # votes needed for a split decision, a paired, fused or alias opcode
MAJORITY = 5             # a split vote is accepted only at this ratio (2.7 point releases changed a few functions)
MIN_KNOWN_OVERLAP = 40   # opcodes a known map must share with the evidence before it may fill gaps


@dataclass
class OpcodeMap:
    """Recovered numbering: opcode -> name or tagged tuple, the evidence for each, and what stays unknown."""

    mapping: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    conflicts: dict = field(default_factory=dict)
    unmapped: dict = field(default_factory=dict)


def expand_fused(value: tuple, arg: int) -> list[tuple[str, int]]:
    """The two instructions a fused opcode stands for."""
    _, first, second, packing = value
    hi, lo = arg >> 8, arg & 0xFF
    return [(first, hi), (second, lo)] if packing == "hi/lo" else [(first, lo), (second, hi)]


def _by_path(code: CodeObject) -> dict[str, list[CodeObject]]:
    found = defaultdict(list)
    for path, obj in code.walk():
        found[path].append(obj)
    return found


def _aligned(code: bytes, ref_code: bytes, name_of) -> list[tuple[int, str, int]] | None:
    """[(opcode, name, run parity)] when both codes match instruction by instruction with equal operands, else
    None. `name_of` gives the reference opcode's name (None when unknown). Run parity is the position of an opcode
    within a run of that same opcode, which tells the two halves of a paired opcode apart."""
    if len(code) != len(ref_code):
        return None
    found, i, prev, run = [], 0, None, 0
    while i < len(ref_code):
        op, ref_op = code[i], ref_code[i]
        name = name_of(ref_op)
        if name is None or (op >= HAVE_ARGUMENT) != (ref_op >= HAVE_ARGUMENT):
            return None
        if ref_op >= HAVE_ARGUMENT:
            if code[i + 1:i + 3] != ref_code[i + 1:i + 3]:
                return None
            i += 3
        else:
            i += 1
        run = run + 1 if op == prev else 0
        prev = op
        found.append((op, name, run % 2))
    return found


def _vote(found, votes, run_votes) -> None:
    for op, name, parity in found:
        votes[op][name] += 1
        run_votes[(op, parity)][name] += 1


def _stdlib_alignment(modules: dict[str, ScriptModule], votes, run_votes) -> int:
    with gzip.open(os.path.join(DATA_DIR, "stdlib27_ref.json.gz"), "rt") as f:
        reference = json.load(f)["modules"]
    matched = 0
    for path, module in modules.items():
        if not path.startswith("lib/") or path[4:] not in reference:
            continue
        ref_paths = defaultdict(list)
        for ref_path, names, varnames, hexcode in reference[path[4:]]:
            ref_paths[ref_path].append((names, varnames, bytes.fromhex(hexcode)))
        for code_path, objs in _by_path(module.code).items():
            refs = ref_paths.get(code_path, ())
            if len(objs) != 1 or len(refs) != 1 or (objs[0].names, objs[0].varnames) != refs[0][:2]:
                continue
            found = _aligned(objs[0].code, refs[0][2], NAME_OF.get)
            if found is not None:
                matched += 1
                _vote(found, votes, run_votes)
    return matched


def _paired_functions(modules, reference_modules, include_stdlib=True):
    """(code, reference code) for functions at the same path that occur once on both sides."""
    for path, module in modules.items():
        other = reference_modules.get(path)
        if other is None or (not include_stdlib and path.startswith("lib/")):
            continue
        theirs = _by_path(other.code)
        for code_path, objs in _by_path(module.code).items():
            refs = theirs.get(code_path, ())
            if len(objs) == 1 and len(refs) == 1:
                yield objs[0], refs[0]


def _build_alignment(modules, reference_modules, reference_map, votes, run_votes) -> int:
    def name_of(op):
        name = reference_map.get(op)
        return name if isinstance(name, str) else None

    matched = 0
    for code, ref in _paired_functions(modules, reference_modules, include_stdlib=False):
        if (code.names, code.varnames) != (ref.names, ref.varnames):
            continue
        found = _aligned(code.code, ref.code, name_of)
        if found is not None:
            matched += 1
            _vote(found, votes, run_votes)
    return matched


def _plain_consts(consts) -> list[str]:
    return [repr(c) for c in consts if not isinstance(c, CodeObject)]


def _same_consts(a, b) -> bool:
    """One constant list extends the other: compilers of a generation sometimes fold one extra constant, which leaves
    every shared index (and so every compared LOAD_CONST argument) unchanged."""
    a, b = _plain_consts(a), _plain_consts(b)
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _diff_units(code, fused_known, known):
    """(opcode or None for half of an expanded fused one, argument, name if known) per instruction."""
    units = []
    for _, op, arg in instructions(code.code):
        if op in fused_known:
            units += [(None, a, n) for n, a in expand_fused(fused_known[op], arg)]
        else:
            units.append((op, arg, known.get(op)))
    return units


def _diff_vote(units, theirs, votes, fused_votes, allow_fused) -> None:
    """Vote from the replaced runs of a sequence diff between our units and the reference's (name, arg) list."""
    matcher = difflib.SequenceMatcher(None, [n or f"?{op}" for op, _, n in units], [n for n, _ in theirs],
                                      autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        seg, ref_seg = units[i1:i2], theirs[j1:j2]
        if tag != "replace" or all(n for _, _, n in seg):
            continue
        if len(seg) == len(ref_seg):
            for (op, arg, name), (ref_name, ref_arg) in zip(seg, ref_seg):
                if name is None and op is not None and arg == ref_arg:
                    votes[op][ref_name] += 1
        elif allow_fused and len(seg) == 1 and len(ref_seg) == 2 and seg[0][1] is not None \
                and None not in (ref_seg[0][1], ref_seg[1][1]):
            op, arg = seg[0][0], seg[0][1]
            (first, a), (second, b) = ref_seg
            if arg == (a << 8 | b):
                fused_votes[op][(first, second, "hi/lo")] += 1
            elif arg == (b << 8 | a):
                fused_votes[op][(first, second, "lo/hi")] += 1


def _diff_alignment(modules, references, votes, fused_votes, fused_known) -> None:
    """Sequence-diff functions that differ from a mapped build only where unknown opcodes sit. A replaced run of
    equal length votes one-for-one; one argument-taking opcode replacing two instructions whose arguments it packs
    votes as fused. Opcodes in `fused_known` are expanded first, so a rare opcode next to a fused one still lines
    up."""
    known = {op: c.most_common(1)[0][0] for op, c in votes.items() if len(c) == 1}
    for reference_modules, reference_map in references:
        for code, ref in _paired_functions(modules, reference_modules):
            if all(op in known or op in fused_known for _, op, _ in instructions(code.code)):
                continue
            if (code.names, code.varnames) != (ref.names, ref.varnames) or not _same_consts(code.consts, ref.consts):
                continue
            theirs = [(reference_map.get(op), arg) for _, op, arg in instructions(ref.code)]
            if all(isinstance(name, str) for name, _ in theirs):
                _diff_vote(_diff_units(code, fused_known, known), theirs, votes, fused_votes, not fused_known)


def _resolve(votes, run_votes, fused_votes, result: OpcodeMap) -> None:
    for op, names in votes.items():
        if len(names) == 1:
            name = next(iter(names))
            result.mapping[op], result.evidence[op] = name, f"alignment ({names[name]} votes)"
            continue
        (top, count), rest = names.most_common(1)[0], sum(names.values()) - names.most_common(1)[0][1]
        first, second = run_votes[(op, 0)], run_votes[(op, 1)]
        if count >= MIN_VOTES and count >= MAJORITY * rest:
            result.mapping[op], result.evidence[op] = top, f"alignment (majority {count}:{rest})"
        elif (len(first) == 1 and len(second) == 1 and min(first.most_common(1)[0][1], second.most_common(1)[0][1])
              >= MIN_VOTES and next(iter(first)) != next(iter(second))):
            pair = ("pair", next(iter(first)), next(iter(second)))
            result.mapping[op] = pair
            result.evidence[op] = f"alignment, paired: {pair[1]} then {pair[2]} ({first.most_common(1)[0][1]} pairs)"
        else:
            result.conflicts[op] = dict(names)
    for op, kinds in fused_votes.items():
        if op in result.mapping or op in result.conflicts:
            continue
        (first, second, packing), count = kinds.most_common(1)[0]
        if len(kinds) == 1 and count >= MIN_VOTES:
            result.mapping[op] = ("fused", first, second, packing)
            result.evidence[op] = f"fused: {first} + {second}, arguments packed {packing} ({count} occurrences)"
    # Aliases (two opcodes for one instruction) stand only with enough unanimous votes.
    plain = Counter(n for n in result.mapping.values() if isinstance(n, str))
    for op in [o for o, n in result.mapping.items() if isinstance(n, str) and plain[n] > 1]:
        if votes[op][result.mapping[op]] < MIN_VOTES:
            result.conflicts[op] = {result.mapping.pop(op): "duplicate name, too few votes"}
            result.evidence.pop(op)
        else:
            result.evidence[op] += "; alias"


def _comprehension_rule(modules, result: OpcodeMap, used: Counter) -> None:
    where, args = defaultdict(set), defaultdict(set)
    for module in modules.values():
        for _, code in module.code.walk():
            for _, op, arg in instructions(code.code):
                where[op].add(code.name)
                args[op].add(arg)
    names = {n for n in result.mapping.values() if isinstance(n, str)}
    for scope, name in (("<dictcomp>", "MAP_ADD"), ("<setcomp>", "SET_ADD")):
        candidates = [op for op in used if op not in result.mapping and op >= HAVE_ARGUMENT
                      and where[op] == {scope} and args[op] == {2}]
        if name not in names and len(candidates) == 1:
            result.mapping[candidates[0]] = name
            result.evidence[candidates[0]] = f"only inside {scope}, always with argument 2"


_INDEX_TABLE = {"LOAD_CONST": "consts", "LOAD_NAME": "names", "STORE_NAME": "names", "LOAD_ATTR": "names",
                "STORE_ATTR": "names", "LOAD_GLOBAL": "names", "STORE_GLOBAL": "names", "IMPORT_NAME": "names",
                "IMPORT_FROM": "names", "LOAD_FAST": "varnames", "STORE_FAST": "varnames"}


def _extended_arg_rule(modules, result: OpcodeMap, used: Counter) -> None:
    if "EXTENDED_ARG" in result.mapping.values():
        return
    fits = Counter()
    for module in modules.values():
        for _, code in module.code.walk():
            ins = list(instructions(code.code))
            for k, (_, op, arg) in enumerate(ins):
                if op in result.mapping or arg is None or k + 1 >= len(ins):
                    continue
                next_name, next_arg = result.mapping.get(ins[k + 1][1]), ins[k + 1][2]
                table = getattr(code, _INDEX_TABLE.get(next_name, ""), None) if isinstance(next_name, str) else None
                if table is not None and len(table) > 0xFFFF and (arg << 16 | next_arg) < len(table):
                    fits[op] += 1
    for op, count in fits.items():
        if count == used[op]:  # every single occurrence fits
            result.mapping[op] = "EXTENDED_ARG"
            result.evidence[op] = f"prefix of a constant/name index beyond 65,535 in all {count} occurrences"
            return


def _known_map(result: OpcodeMap, used: Counter) -> None:
    with open(os.path.join(DATA_DIR, "opmap_known.json"), encoding="utf-8") as f:
        known = json.load(f)
    for label, numbering in known.items():
        numbering = {int(op): name for op, name in numbering.items()}
        plain = {op: n for op, n in result.mapping.items() if isinstance(n, str)}
        overlap = sum(1 for op in plain if op in numbering)
        if overlap < MIN_KNOWN_OVERLAP or any(numbering.get(op, n) != n for op, n in plain.items()):
            continue
        for op, name in numbering.items():
            if op in used and op not in result.mapping and name not in plain.values():
                result.mapping[op] = name
                result.evidence[op] = f"known map '{label}' (agrees on all {overlap} recovered opcodes)"


def recover_opcode_map(modules: dict[str, ScriptModule],
                       references: Iterable[tuple[dict[str, ScriptModule], dict]] = ()) -> OpcodeMap:
    """Recover `modules`' numbering; `references` are (modules, mapping) of builds already mapped."""
    log = get_logger()
    references = list(references)
    votes, run_votes, fused_votes = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    matched = _stdlib_alignment(modules, votes, run_votes)
    log.info("Script opcodes: %d bundled standard-library functions match CPython 2.7.18", matched)
    for reference_modules, reference_map in references:
        n = _build_alignment(modules, reference_modules, reference_map, votes, run_votes)
        log.info("Script opcodes: %d functions match the reference build", n)
    if references:
        _diff_alignment(modules, references, votes, fused_votes, {})
        provisional = {op: ("fused", *kinds.most_common(1)[0][0]) for op, kinds in fused_votes.items()
                       if len(kinds) == 1 and kinds.most_common(1)[0][1] >= MIN_VOTES and op not in votes}
        if provisional:
            _diff_alignment(modules, references, votes, fused_votes, provisional)

    result = OpcodeMap()
    _resolve(votes, run_votes, fused_votes, result)
    used = Counter(op for m in modules.values() for _, c in m.code.walk() for _, op, _ in instructions(c.code))
    _comprehension_rule(modules, result, used)
    _extended_arg_rule(modules, result, used)
    _known_map(result, used)
    result.mapping = dict(sorted(result.mapping.items()))
    result.unmapped = {op: n for op, n in used.most_common() if op not in result.mapping}
    if result.unmapped:
        log.warning("Script opcodes still unknown (modules using them are skipped): %s", result.unmapped)
    else:
        log.info("Script opcodes: all %d opcodes of this build are mapped", len(used))
    return result
