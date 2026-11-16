"""Decompile a 2017-2018 Identity V ``script.npk`` to verified Python 2 source.

Stage 1 decompiles every module with uncompyle6 (on bytecode whose dead ``JUMP_FORWARD 0`` are blanked) and, when
a Python 2.7 interpreter is available, verifies every function by recompiling it (``py27/assemble.py``). Stage 2
adds uncompyle6 on the unmodified bytecode and uncompyle2 (when its source folder is given) for modules with an
unverified function or awkward ``continue``/``else`` shapes; each function is then taken from the most readable
candidate whose bytecode equals the original. Game code is never imported or executed: decompilers read it and
``compile()`` checks it.

Output: ``source/<module path>``, ``REPORT.md``, ``opmap.json`` and ``verification.jsonl``.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import datetime
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import traceback
from collections import Counter

from core.logger import get_logger
from core.npk.class_types import NPKReadOptions
from core.npk.npk_file import NPKFile

from .bytecode import ScriptModule, load_modules
from .opmap import OpcodeMap, recover_opcode_map
from .rewrite import RewriteError, to_pyc, unmapped_opcodes

PY27_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "py27")
# Worker processes are started from a thread pool: forking a multi-threaded process can copy a lock another thread
# holds and hang the child, so use a fresh interpreter (forkserver where available, else spawn).
_WORKERS = multiprocessing.get_context(
    "forkserver" if "forkserver" in multiprocessing.get_all_start_methods() else "spawn")
# Decompiling a huge data table can take tens of GB; each decompiler process gets an address-space cap (POSIX) so
# it fails on its own instead of exhausting memory for everything else.
MEMORY_LIMIT = 4 << 30
RETRY_JOBS = 2


def _retry_memory_limit() -> int:
    """Failed modules are retried two at a time with a third of physical memory each (at least MEMORY_LIMIT)."""
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return MEMORY_LIMIT
    return max(MEMORY_LIMIT, total // (RETRY_JOBS + 1))


def _cap_memory(limit: int = MEMORY_LIMIT) -> None:
    try:
        import resource  # pylint: disable=import-outside-toplevel  # POSIX only
    except ImportError:
        return
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
UNCOMPYLE6_DUMP = "-- Stacks of completed symbols:"
CODE_START = re.compile(r"^(import |from |class |def |@|if |try:|for |while |with |print|[A-Za-z_][\w.]*\s*(=|\(|\[))")
AWKWARD = (re.compile(r"^(\s+)pass\n\s*else:", re.M),
           re.compile(r"^\s+(?:return|raise|continue|break)\b[^\n]*\n\s*else:\s*$", re.M))
DECOMPILER_BANNER = re.compile(r"#\s*(uncompyle6 version|Python bytecode version|Decompiled from:|"
                               r"Embedded file name:|okay decompiling)")


def read_archive(path: str) -> dict[str, ScriptModule]:
    """The script modules of an NPK archive, decrypted by NeoXtractor's reader."""
    npk = NPKFile(path, NPKReadOptions())
    with open(path, "rb") as f:
        for i in range(npk.file_count):
            if not npk.is_entry_loaded(i):
                npk.load_entry(i, f)
    return load_modules(entry.data for entry in npk.entries.values())


def comment_failure_dumps(text: str) -> str:
    """uncompyle6 prints grammar and instruction dumps for code it cannot parse; keep them as comments. Also drop
    the bare ``return`` it prints for a module's implicit ``return None``."""
    out, inside = [], False
    for line in text.split("\n"):
        if line.startswith(UNCOMPYLE6_DUMP):
            inside = True
            out.append("# [NeoXtractor] decompiler diagnostic for a function below:")
            continue
        if inside and CODE_START.match(line) and "::=" not in line:
            inside = False
        out.append("# " + line if inside else line)
    for k in range(len(out) - 1, -1, -1):
        if out[k].strip() and not out[k].startswith("#"):
            if out[k] == "return":
                del out[k]
            break
    return "\n".join(out)


def _uncompyle6_worker(pyc: str, target: str, memory_limit: int) -> None:
    from uncompyle6.main import decompile_file  # pylint: disable=import-outside-toplevel

    _cap_memory(memory_limit)
    with open(target, "w", encoding="utf-8") as out, contextlib.redirect_stdout(out):
        try:
            decompile_file(pyc, out)
        except Exception:  # pylint: disable=broad-exception-caught  # kept as a comment in the output
            out.write("\n# [NeoXtractor] uncompyle6 failed:\n"
                      + "".join(f"# {line}\n" for line in traceback.format_exc().splitlines()[-3:]))


def run_uncompyle6(pyc: str, target: str, timeout: int, memory_limit: int = MEMORY_LIMIT) -> bool:
    """uncompyle6 in a short-lived process, so a hang can be stopped and no executable is needed. False when it
    timed out or crashed (the candidate is then empty)."""
    worker = _WORKERS.Process(target=_uncompyle6_worker, args=(pyc, target, memory_limit), daemon=True)
    worker.start()
    worker.join(timeout)
    timed_out = worker.is_alive()
    if timed_out:
        worker.terminate()
        worker.join()
    if timed_out or worker.exitcode != 0:
        with open(target, "w", encoding="utf-8"):
            pass
        return False
    if os.path.exists(target):
        with open(target, encoding="utf-8", errors="replace") as f:
            text = f.read()
        with open(target, "w", encoding="utf-8") as f:
            f.write(comment_failure_dumps(text))
    return True


def run_uncompyle2(python2: str, uncompyle2: str, pyc: str, target: str, timeout: int,
                   memory_limit: int = MEMORY_LIMIT) -> bool:
    """uncompyle2 1.1 under Python 2.7 (py27/run_uncompyle2.py); False when it failed or timed out."""
    try:
        done = subprocess.run([python2, "-E", "-s", "-B", os.path.join(PY27_DIR, "run_uncompyle2.py"), uncompyle2,
                               pyc, target, str(memory_limit)], capture_output=True, timeout=timeout, check=False)
        return done.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _write_pycs(modules, paths, mapping, work) -> tuple[list[str], dict[str, str]]:
    """Rewrite each module twice (as shipped, and with dead jumps blanked); returns (decompilable, refused)."""
    ready, refused = [], {}
    for path in paths:
        unknown = unmapped_opcodes(modules[path], mapping)
        if unknown:
            refused[path] = f"unmapped opcodes {unknown}"
            continue
        try:
            pycs = {"raw": to_pyc(modules[path], mapping), "patched": to_pyc(modules[path], mapping, True)}
        except RewriteError as e:
            refused[path] = f"cannot rewrite exactly: {e}"
            continue
        for kind, data in pycs.items():
            target = os.path.join(work, kind, path + "c")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)
        ready.append(path)
    return ready, refused


def _decompile(jobs, work, paths, settings) -> set[tuple[str, str]]:
    """Run each (candidate, pyc kind) decompiler over `paths` in parallel; output in work/<candidate>/. Returns the
    (candidate, module) pairs that failed or timed out."""
    def one(candidate, kind, path, memory_limit):
        pyc, target = os.path.join(work, kind, path + "c"), os.path.join(work, candidate, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if candidate == "u2":
            ok = run_uncompyle2(settings["python2"], settings["uncompyle2"], pyc, target, settings["timeout"],
                                memory_limit)
        else:
            ok = run_uncompyle6(pyc, target, settings["timeout"], memory_limit)
        return None if ok else (candidate, path)

    def run_all(tasks, workers):
        failed = set()
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            futures = [pool.submit(one, *task) for task in tasks]
            for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
                if future.result():
                    failed.add(future.result())
                if done % 500 == 0:
                    log.info("  %d/%d", done, len(futures))
        return failed

    log = get_logger()
    log.info("Decompiling %d modules with %s", len(paths), ", ".join(c for c, _ in jobs))
    kinds = dict(jobs)
    failed = run_all([(c, k, p, MEMORY_LIMIT) for c, k in jobs for p in paths], settings["jobs"])
    if failed:  # once more, two at a time with more memory: neighbours or a huge table can sink a module
        log.info("Retrying %d failed decompiles", len(failed))
        failed = run_all([(c, kinds[c], p, _retry_memory_limit()) for c, p in sorted(failed)], RETRY_JOBS)
    for candidate, path in sorted(failed):
        log.warning("%s failed or timed out on %s", candidate, path)
    return failed


def _assemble(paths, candidates, work, source_root, settings) -> list[dict]:
    """Verify and assemble with Python 2.7 (py27/assemble.py), in parallel chunks; returns the module reports."""
    chunks = [paths[i::settings["jobs"]] for i in range(settings["jobs"])]

    def chunk(i, modules):
        listing, report = os.path.join(work, f"chunk{i}.txt"), os.path.join(work, f"chunk{i}.jsonl")
        with open(listing, "w", encoding="utf-8") as f:
            f.write("\n".join(modules))
        subprocess.run([settings["python2"], "-E", "-s", "-B", os.path.join(PY27_DIR, "assemble.py"),
                        os.path.join(work, "raw"), source_root, report, listing]
                       + [f"{c}={os.path.join(work, c)}" for c in candidates],
                       check=False, timeout=max(600, settings["timeout"] * len(modules)))
        if not os.path.exists(report):
            return []
        with open(report, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    with concurrent.futures.ThreadPoolExecutor(settings["jobs"]) as pool:
        return [r for rs in pool.map(chunk, range(len(chunks)), chunks) if rs for r in rs]


def _needs_stage_two(report, source_root) -> bool:
    if not report.get("compiles") or report.get("verified") != report.get("functions"):
        return True
    path = os.path.join(source_root, report["module"])
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    return any(pattern.search(text) for pattern in AWKWARD)


def _write_headers(modules, paths, reports, source_root, summary) -> None:
    by_module = {r["module"]: r for r in reports if "module" in r}
    label, sha256 = summary["archive"], summary["sha256"]
    archive = f"{label} (sha256 {sha256[:16]}...)" if sha256 else label
    for path in paths:
        target = os.path.join(source_root, path)
        if not os.path.exists(target):
            continue
        report = by_module.get(path, {})
        verified = (f"{report['verified']} of {report['functions']} functions verified by recompilation"
                    if report.get("functions") is not None else "not verified (no Python 2.7)")
        with open(target, encoding="utf-8", errors="replace") as f:
            body = "\n".join(line for line in f.read().split("\n") if not DECOMPILER_BANNER.match(line))
        with open(target, "w", encoding="utf-8") as f:
            f.write(f"# {path}\n# Decompiled by NeoXtractor from {archive}; module sha256 "
                    f"{modules[path].sha256[:16]}...\n# {verified}\n\n{body.strip()}\n")


def _write_report(out_dir, summary, opcode_map: OpcodeMap, reports, details) -> None:
    refused, skipped, candidates = details["refused"], details["skipped"], details["candidates"]
    picks = Counter(d["chosen"] for r in reports for d in (r.get("detail") or {}).values())
    verified, total = summary["verified"], summary["functions"]
    lines = ["# Script decompile report", "",
             f"- Archive: `{summary['archive']}`" + (f", sha256 `{summary['sha256']}`" if summary["sha256"] else ""),
             f"- Date: {datetime.date.today().isoformat()}",
             f"- Modules: {summary['modules']} in the archive, {summary['decompiled']} decompiled, "
             f"{len(refused)} refused, {len(skipped)} skipped as too large",
             f"- Opcode map: {len(opcode_map.mapping)} opcodes mapped, {len(opcode_map.unmapped)} unmapped "
             "(evidence in `opmap.json`)",
             f"- Decompilers: {', '.join(candidates)}"]
    if summary["verified_run"]:
        lines += [f"- Functions verified by recompilation: **{verified} of {total}** "
                  f"({100.0 * verified / max(total, 1):.1f}%)",
                  f"- Modules whose assembled source compiles as Python 2.7: "
                  f"{sum(1 for r in reports if r.get('compiles'))} of {summary['decompiled']}",
                  "- Verified functions taken from: " + ", ".join(f"{k} {v}" for k, v in picks.most_common() if k)]
    lines += ["", "## Refused modules", ""] + [f"- `{p}`: {why}" for p, why in sorted(refused.items())]
    lines += ["", "## Skipped modules", ""] + [f"- `{p}`" for p in skipped]
    lines += ["", "## Modules no decompiler could handle (failed or timed out)", ""] + [
        f"- `{p}`" for p in summary["failed"]]
    if summary["verified_run"]:
        lines += ["", "## Unverified functions", "",
                  "Marked `# [NeoXtractor] UNVERIFIED` in the source: recompiling them does not reproduce the "
                  "original bytecode, so they may be wrong.", ""]
        for r in sorted(reports, key=lambda r: r.get("module", "")):
            bad = [k for k, d in (r.get("detail") or {}).items() if not d["chosen"]]
            if bad:
                lines.append(f"- `{r['module']}`: " + ", ".join(f"`{b}`" for b in bad))
    with open(os.path.join(out_dir, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "opmap.json"), "w", encoding="utf-8") as f:
        json.dump({"archive": summary["archive"], "sha256": summary["sha256"],
                   "mapping": {str(k): v for k, v in opcode_map.mapping.items()},
                   "evidence": {str(k): v for k, v in opcode_map.evidence.items()},
                   "conflicts": {str(k): v for k, v in opcode_map.conflicts.items()},
                   "unmapped": {str(k): v for k, v in opcode_map.unmapped.items()}}, f, indent=1)
    with open(os.path.join(out_dir, "verification.jsonl"), "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in reports)


def _run_stages(work, source_root, ready, settings) -> tuple[list[dict], list[str], list[str]]:
    """Stage 1 (uncompyle6, verified) and, where needed, stage 2 (more decompilers). Returns (reports, candidates,
    modules every decompiler failed on)."""
    candidates = ["u6"]
    failed = _decompile([("u6", "patched")], work, ready, settings)
    if not settings["python2"]:
        get_logger().warning("No Python 2.7: functions are not verified")
        for path in ready:
            os.makedirs(os.path.dirname(os.path.join(source_root, path)), exist_ok=True)
            shutil.copyfile(os.path.join(work, "u6", path), os.path.join(source_root, path))
        return [], candidates, sorted(p for _, p in failed)
    first = {r["module"]: r for r in _assemble(ready, candidates, work, source_root, settings) if "module" in r}
    stage_one_failed = {p for _, p in failed}
    retry = [p for p in ready if p in stage_one_failed or p not in first or _needs_stage_two(first[p], source_root)]
    get_logger().info("Stage 1: %d of %d modules fully verified; %d go to stage 2",
                      len(ready) - len(retry), len(ready), len(retry))
    reports = [first[p] for p in ready if p in first and p not in retry]
    if retry:
        stage_two = [("u6raw", "raw")] + ([("u2", "raw")] if settings["uncompyle2"] else [])
        failed |= _decompile(stage_two, work, retry, settings)
        candidates += [c for c, _ in stage_two]
        reports += _assemble(retry, candidates, work, source_root, settings)
    all_failed = sorted(p for p in ready if all((c, p) in failed for c in candidates))
    return reports, candidates, all_failed


def decompile_scripts(modules: dict[str, ScriptModule], out_dir: str, *, label: str = "script.npk",
                      archive_sha256: str | None = None, references=(), python2: str | None = None,
                      uncompyle2: str | None = None, only=(), include_stdlib: bool = False, max_size: int = 0,
                      jobs: int | None = None, timeout: int = 300, keep_work: bool = False) -> dict:
    """Decompile `modules` into `out_dir` (which must not exist). `references` are (modules, mapping) of builds
    already mapped (needed for Feb-May 2018). Returns a summary dict."""
    os.makedirs(out_dir)
    work, source_root = os.path.join(out_dir, "_work"), os.path.join(out_dir, "source")
    settings = {"python2": python2, "uncompyle2": uncompyle2, "timeout": timeout, "jobs": jobs or os.cpu_count() or 4}

    opcode_map = recover_opcode_map(modules, references)
    selected = sorted(p for p in modules if (include_stdlib or not p.startswith("lib/"))
                      and (not only or any(s.lower() in p.lower() for s in only)))
    skipped = [p for p in selected if max_size and len(modules[p].data) > max_size]
    ready, refused = _write_pycs(modules, [p for p in selected if p not in skipped], opcode_map.mapping, work)
    get_logger().info("Scripts: %d modules to decompile, %d refused, %d skipped", len(ready), len(refused),
                      len(skipped))
    reports, candidates, all_failed = _run_stages(work, source_root, ready, settings)

    summary = {"archive": label, "sha256": archive_sha256, "modules": len(modules), "decompiled": len(ready),
               "refused": refused, "functions": sum(r.get("functions") or 0 for r in reports),
               "verified": sum(r.get("verified") or 0 for r in reports), "verified_run": bool(python2)}
    _write_headers(modules, ready, reports, source_root, summary)
    summary["failed"] = all_failed
    _write_report(out_dir, summary, opcode_map, reports,
                  {"refused": refused, "skipped": skipped, "candidates": candidates})
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    get_logger().info("Scripts: done, report in %s", os.path.join(out_dir, "REPORT.md"))
    return summary


def decompile_archive(path: str, out_dir: str, *, reference_archives=(), **options) -> dict:
    """Read `path` with NeoXtractor's reader and decompile it; `reference_archives` are script.npk paths of builds
    whose numbering is recovered first and used as references."""
    references = []
    for reference in reference_archives:
        reference_modules = read_archive(reference)
        references.append((reference_modules, recover_opcode_map(reference_modules).mapping))
    with open(path, "rb") as f:
        sha256 = hashlib.file_digest(f, "sha256").hexdigest()
    return decompile_scripts(read_archive(path), out_dir, label=os.path.basename(path), archive_sha256=sha256,
                             references=references, **options)
