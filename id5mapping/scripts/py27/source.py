# -*- coding: utf-8 -*-
"""Read decompiled Python 2 source text (runs under Python 2.7).

Decompiler output may contain failure dumps or a statement that does not parse, so function blocks are found with
a small error-tolerant lexer instead of the ``tokenize`` module. Two cosmetic clean-ups that compile to identical
bytecode are applied to every candidate before it is verified.
"""

import re

# Decompiler failure dumps (instruction listings, error banners) are not code.
FAILURE_DUMP = re.compile(r'^(\s*(\d+(_\d+)?|L\.\s*\d+)\s+[A-Z_]{3,}|Syntax error at or near|\s*-->|'
                          r'\s*--- This code section)')
STRING_LITERAL = re.compile(r"""(?<![\w'"])([bB]?)('|")((?:\\.|(?!\2)[^\\\n])*)\2""")
ESCAPE_RUN = re.compile(r'(?:\\x[89a-fA-F][0-9a-fA-F])+')
PASS_ELSE = re.compile(r'^(\s+)pass\n\s*else:', re.M)
ELSE_AFTER_EXIT = re.compile(r'^\s+(?:return|raise|continue|break)\b[^\n]*\n\s*else:\s*$', re.M)


def _scan(line, state):
    """Advance the lexer over one line. state = [bracket depth, open triple quote or None]."""
    i, single = 0, None
    while i < len(line):
        ch = line[i]
        if state[1]:
            if line.startswith(state[1], i):
                i, state[1] = i + 3, None
                continue
            i += 2 if ch == '\\' else 1
            continue
        if single:
            if ch == '\\':
                i += 2
                continue
            if ch == single:
                single = None
            i += 1
            continue
        if ch == '#':
            break
        if line.startswith('"""', i) or line.startswith("'''", i):
            state[1] = line[i:i + 3]
            i += 3
            continue
        if ch in '"\'':
            single = ch
        elif ch in '([{':
            state[0] += 1
        elif ch in ')]}':
            state[0] = max(0, state[0] - 1)
        i += 1
    if single:  # an unterminated string on a broken line: resynchronise
        state[0] = 0


def logical_lines(text):
    """[(row, indent, first word)] for the first physical line of each logical line; rows are 1-based."""
    out, state, continued = [], [0, None], False
    for row, line in enumerate(text.split('\n'), 1):
        if FAILURE_DUMP.match(line):
            continue
        if state[0] == 0 and state[1] is None and not continued:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                word = re.match(r'(@|[A-Za-z_]\w*)', stripped)
                out.append((row, len(line) - len(line.lstrip(' ')), word.group(1) if word else stripped[:1]))
        _scan(line, state)
        continued = line.rstrip().endswith('\\') and state[1] is None
    return out


def function_blocks(text):
    """{(class, ..., name, occurrence): (first row, last row, indent)} for functions defined at module level or in
    classes; decorators are part of the block."""
    lines, logical = text.split('\n'), logical_lines(text)
    found, stack, seen = {}, [], {}
    for k, (row, indent, word) in enumerate(logical):
        while stack and stack[-1][1] >= indent:
            stack.pop()
        if word not in ('def', 'class'):
            continue
        match = re.match(r'\s*(?:def|class)\s+(\w+)', lines[row - 1])
        if not match:
            continue
        name = match.group(1)
        last = next((r - 1 for r, i, _ in logical[k + 1:] if i <= indent), len(lines))
        first, j = row, k - 1
        while j >= 0 and logical[j][2] == '@' and logical[j][1] == indent:
            first, j = logical[j][0], j - 1
        classes = tuple(s[0] for s in stack if s[2] == 'class')
        if word == 'def' and not any(s[2] == 'def' for s in stack):
            occurrence = seen.get((classes, name), 0)
            seen[(classes, name)] = occurrence + 1
            found[classes + (name, occurrence)] = (first, last, indent)
        stack.append((name, indent, word))
    return found


def block_lines(lines, block, new_indent):
    """A function block's lines re-indented from its own indent to `new_indent`, trailing blank lines dropped."""
    first, last, indent = block
    out = []
    for line in lines[first - 1:last]:
        lead = len(line) - len(line.lstrip(' '))
        out.append(' ' * new_indent + line[min(lead, indent):] if line.strip() else '')
    while out and not out[-1].strip():
        out.pop()
    return out


def _decode_escape_run(run):
    raw = ''.join(chr(int(run[i + 2:i + 4], 16)) for i in range(0, len(run), 4))
    try:
        raw.decode('utf-8')
        return raw
    except UnicodeDecodeError:
        return run


def unescape_utf8(text):
    """Write byte-string literals spelled as ``\\xNN`` escapes of valid UTF-8 (uncompyle2 prints Chinese that way)
    as the characters themselves. Python 2 compiles both spellings of a plain str literal to the same bytes;
    unicode and raw literals are left alone."""
    def literal(match):
        body, out, i = match.group(3), [], 0
        for run in ESCAPE_RUN.finditer(body):
            before = body[i:run.start()]
            backslashes = len(before) - len(before.rstrip('\\'))  # an escaped backslash is not an escape
            out.append(before)
            out.append(_decode_escape_run(run.group(0)) if backslashes % 2 == 0 else run.group(0))
            i = run.end()
        out.append(body[i:])
        return match.group(1) + match.group(2) + ''.join(out) + match.group(2)
    return STRING_LITERAL.sub(literal, text)


def strip_trailing_returns(text):
    """Drop a bare ``return`` that ends a function body (uncompyle6 prints the implicit ``return None``); a body left
    empty gets ``pass``."""
    lines, logical, replace = text.split('\n'), logical_lines(text), {}
    for k, (_, indent, word) in enumerate(logical):
        if word != 'def' or k + 1 >= len(logical) or logical[k + 1][1] <= indent:
            continue
        body = []
        for row, i, _ in logical[k + 1:]:
            if i <= indent:
                break
            body.append((row, i))
        body_indent = logical[k + 1][1]
        last_row, last_indent = body[-1]
        if last_indent == body_indent and lines[last_row - 1].strip() == 'return':
            only_statement = sum(1 for _, i in body if i == body_indent) == 1
            replace[last_row] = ' ' * body_indent + 'pass' if only_statement else None
    out = []
    for row, line in enumerate(lines, 1):
        if row not in replace:
            out.append(line)
        elif replace[row] is not None:
            out.append(replace[row])
    return '\n'.join(out)


def tidy(text):
    return strip_trailing_returns(unescape_utf8(text))


def readability_penalty(lines):
    """Lower is better: ``pass``/``else:`` detours (a ``continue`` decompiled as nesting), an ``else:`` after an
    exit, leftover escaped strings, then length."""
    text = '\n'.join(lines)
    return (5 * len(PASS_ELSE.findall(text)) + 3 * len(ELSE_AFTER_EXIT.findall(text))
            + 2 * len(ESCAPE_RUN.findall(text)) + 0.02 * len(lines))
