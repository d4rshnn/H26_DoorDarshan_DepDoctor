"""Stage 3: apply each accepted proposal to a sandbox copy, re-run the tests, compare with the baseline.

The repo itself is never modified. Each proposal gets workdir/<id>/src (the patched copy) and
workdir/<id>/run (the venv and test run made by baseline.run). The change rules are the ones
in proposals.py, so a proposal that passed validation applies here the same way.
"""

import difflib
import os
import re
import shutil
import stat
from pathlib import Path

from . import baseline
from .proposals import ACTIONS, apply_change, check_path

SCHEMA = 1
PROVENANCE = "executed"
IGNORE = (".git", ".venv", "venv", "__pycache__", ".sandbox", ".pytest_cache", "*.egg-info")


def _read(path):
    # newline="" keeps CRLF/LF exactly as they are; surrogateescape round-trips non-UTF-8 bytes
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)


def copy_repo(repo, dest, workdir):
    """Copy repo to dest, skipping caches, venvs and the workdir itself if it lies inside the repo."""
    patterns = set(IGNORE)
    try:
        rel = Path(workdir).resolve().relative_to(Path(repo).resolve())
        if rel.parts:
            patterns.add(rel.parts[0])
    except ValueError:
        pass
    shutil.copytree(repo, dest, ignore=shutil.ignore_patterns(*patterns))


def make_diff(originals, patched):
    """Unified diff of every changed file; a new file is diffed against /dev/null."""
    out = []
    for rel in sorted(patched):
        before, after = originals[rel], patched[rel]
        if before == after:
            continue
        lines = difflib.unified_diff((before or "").splitlines(keepends=True), after.splitlines(keepends=True),
                                     fromfile="/dev/null" if before is None else f"a/{rel}", tofile=f"b/{rel}")
        out.extend(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    return "".join(out)


def apply_proposal(src, proposal):
    """Apply a proposal's changes to the copy at src. Return (ok, reason, diff).

    All changes are applied in memory first; files are written only if every change applies.
    """
    changes = proposal.get("changes") if isinstance(proposal, dict) else None
    if not (isinstance(changes, list) and changes):
        return False, "proposal has no changes", ""
    originals, current = {}, {}
    for n, change in enumerate(changes, 1):
        if not isinstance(change, dict) or change.get("action") not in ACTIONS:
            return False, f"change {n}: unknown or missing action", ""
        missing = [f for f in ACTIONS[change["action"]] if not isinstance(change.get(f), str)]
        if missing:
            return False, f"change {n}: {change['action']} needs {missing}", ""
        rel, reason = check_path(src, change.get("file"))
        if reason:
            return False, f"change {n}: {reason}", ""
        if rel not in current:
            path = src / rel
            originals[rel] = current[rel] = _read(path) if path.is_file() else None
        new_text, error = apply_change(current[rel], change)
        if error:
            return False, f"change {n} ({rel}): {error}", ""
        current[rel] = new_text
    try:
        for rel, text in current.items():
            if text is not None and text != originals[rel]:
                _write(src / rel, text)
    except OSError as exc:
        return False, f"could not write the patched file: {exc}", ""
    return True, "", make_diff(originals, current)


def _failing(run):
    return {f.get("test") for f in ((run or {}).get("pytest") or {}).get("failures") or []}


def _ran(run):
    pytest = (run or {}).get("pytest") or {}
    return pytest.get("passed") is not None and "skipped" not in pytest


def compare(before, after):
    """Return (fixed_tests, newly_broken_tests, verdict, reason) for two baseline-shaped runs."""
    if not _ran(after):
        skipped = ((after or {}).get("pytest") or {}).get("skipped") or "pytest did not finish"
        return [], [], "could_not_run", f"tests did not run after the change: {skipped}"
    if not _ran(before):
        return [], [], "could_not_run", "the baseline has no test results to compare with"

    was, now = _failing(before), _failing(after)
    fixed, broken = sorted(was - now), sorted(now - was)
    b, a = before["pytest"], after["pytest"]
    problems = []
    if broken:
        problems.append(f"{len(broken)} test(s) newly failing")
    if (before.get("install") or {}).get("exit_code") == 0 and (after.get("install") or {}).get("exit_code") != 0:
        problems.append("install now fails")
    if a["passed"] < b["passed"]:
        problems.append(f"passed dropped from {b['passed']} to {a['passed']}")
    counts = f"passed {b['passed']} -> {a['passed']}, failed {b.get('failed')} -> {a.get('failed')}"
    if problems:
        return fixed, broken, "regressed", "; ".join(problems) + f" ({counts})"
    if fixed:
        return fixed, broken, "improved", f"{len(fixed)} test(s) fixed, none newly failing ({counts})"
    return fixed, broken, "unchanged", f"no test changed state ({counts})"


def _dir_name(pid, n, used):
    """A safe, unique folder name for a proposal id (ids come from the model)."""
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(pid or ""))
    if name.strip(".") == "":
        name = f"proposal{n}"
    while name in used:
        name += "_"
    used.add(name)
    return name


def _remove(path):
    """Delete a previous sandbox for this proposal (clearing read-only flags left by venvs on Windows)."""
    def clear_readonly(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=clear_readonly)


def verify_one(repo, proposal, before, root, python, baseline_run):
    pid = proposal.get("id") if isinstance(proposal, dict) else None
    result = {"proposal_id": pid, "applied_diff": "", "apply_ok": False, "after": None, "fixed_tests": [],
              "newly_broken_tests": [], "verdict": "could_not_run", "reason": "", "provenance": PROVENANCE}
    src = root / "src"
    try:
        _remove(root)
        copy_repo(repo, src, root.parent)
    except OSError as exc:
        result["reason"] = f"could not copy the repo: {exc}"
        return result

    ok, reason, diff = apply_proposal(src, proposal)
    result["apply_ok"], result["applied_diff"] = ok, diff
    if not ok:
        result["reason"] = f"fix did not apply: {reason}"
        return result

    try:
        after = baseline_run(src, python, root / "run")
    except Exception as exc:  # the test run must not take verify down with it
        result["reason"] = f"test run failed to start: {exc}"
        return result
    result["after"] = after
    (result["fixed_tests"], result["newly_broken_tests"],
     result["verdict"], result["reason"]) = compare(before, after)
    return result


def run(repo_path, proposals, baseline_result, workdir, python="3.12", baseline_run=baseline.run):
    """Verify every accepted proposal. Returns a dict in the verify.json shape; does not raise for
    proposals that fail to apply or tests that cannot run."""
    repo, workdir = Path(repo_path), Path(workdir)
    if not repo.is_dir():
        raise FileNotFoundError(f"not a directory: {repo_path}")
    rejected = {r.get("id") for r in proposals.get("rejected", []) if isinstance(r, dict) and r.get("id")}
    results, used = [], set()
    for n, proposal in enumerate(proposals.get("proposals") or [], 1):
        pid = proposal.get("id") if isinstance(proposal, dict) else None
        if isinstance(pid, str) and pid in rejected:
            continue
        root = workdir / _dir_name(pid, n, used)
        results.append(verify_one(repo, proposal, baseline_result, root, python, baseline_run))
    return {"schema": SCHEMA, "results": results}
