"""Verifier canary: a negative control that proves the verifier can fail.

The canary deletes a requirement the code really imports, runs it through the same verify step as
the real proposals (in its own sandbox copy), and checks that the verdict is "regressed". If the
verifier calls a known-bad change anything else, its other verdicts cannot be trusted either.

The canary is never written to proposals.json and is never counted as a fix.
"""

import re
from pathlib import Path

from . import verify as verify_mod
from .scan import canonical

EXPECTED = "regressed"
PREFERRED = ("click", "rich", "requests")
# package -> packages that pull it in as a dependency; removing it from requirements.txt would not
# break anything while one of these is still declared, so it would make a useless canary
KNOWN_DEPENDANTS = {
    "certifi": {"requests"}, "charset-normalizer": {"requests"}, "idna": {"requests"},
    "urllib3": {"requests"}, "markupsafe": {"jinja2"}, "pygments": {"rich"}, "markdown-it-py": {"rich"},
    "six": {"python-dateutil"}, "colorama": {"click", "pytest"}, "typing-extensions": {"pydantic"},
    "packaging": {"pytest"}, "pluggy": {"pytest"}, "iniconfig": {"pytest"},
}
NEVER = {"pytest", "pip", "setuptools", "wheel"}  # the test runner and build tools are not canaries
PLAIN_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*((?:==|>=)\s*[A-Za-z0-9.*+!-]+)?\s*$")


def _is_test_file(path):
    name = Path(path).name
    return path.startswith("tests/") or name.startswith("test_") or name == "conftest.py"


def _candidates(evidence, repo_path):
    """Plain requirements.txt lines for packages imported by non-test code: [(package, line, lineno)]."""
    req = Path(repo_path) / "requirements.txt"
    if not req.is_file():
        return []
    lines = req.read_text(encoding="utf-8", errors="replace").splitlines()
    declared = {canonical(m["package"]) for m in evidence.get("manifests", [])}
    imported = {canonical(u["package"]) for u in evidence.get("usage", []) if not _is_test_file(u["file"])}
    found = []
    for m in evidence.get("manifests", []):
        if m.get("file") != "requirements.txt" or not m.get("line") or m["line"] > len(lines):
            continue
        line, key = lines[m["line"] - 1], canonical(m["package"])
        if not PLAIN_LINE.match(line) or canonical(PLAIN_LINE.match(line).group(1)) != key:
            continue
        if key in NEVER or key not in imported or KNOWN_DEPENDANTS.get(key, set()) & (declared - {key}):
            continue
        found.append((m["package"], line.strip(), m["line"]))
    return found


def build_canary(evidence, repo_path):
    """A proposal-shaped dict that deletes one imported requirement, or None if no package is usable."""
    found = _candidates(evidence or {}, repo_path)
    if not found:
        return None
    by_key = {canonical(p): (p, line, n) for p, line, n in found}
    package, line, lineno = next((by_key[k] for k in PREFERRED if k in by_key), found[0])
    return {
        "id": "canary",
        "problem": f"negative control: remove {package}, which the code imports",
        "kind": "remove_unused",
        "evidence_refs": [f"manifest:requirements.txt:{lineno}"],
        "changes": [{"file": "requirements.txt", "action": "delete_line", "old": line}],
        "rationale": "negative control: removes a package the code imports; a working verifier must flag this "
                     "as a regression",
        "risk": "deliberately harmful; never applied to the target",
    }


def _counts(run):
    pytest = (run or {}).get("pytest") or {}
    return {k: pytest.get(k) for k in ("exit_code", "passed", "failed", "errors")}


def run_canary(repo, evidence, baseline, workdir, python="3.12", verify_run=verify_mod.run):
    """Run the canary through verify. `detected` is True/False, or None when it could not be judged."""
    proposal = build_canary(evidence, repo)
    if proposal is None:
        return {"package": None, "line_removed": None, "verdict": None, "expected": EXPECTED, "detected": None,
                "reason": "no suitable package", "provenance": "unverified"}
    line = proposal["changes"][0]["old"]
    result = {"package": PLAIN_LINE.match(line).group(1), "line_removed": line, "expected": EXPECTED,
              "before": _counts(baseline)}
    try:
        out = verify_run(repo, {"proposals": [proposal]}, baseline, workdir, python=python)
        r = out["results"][0]
    except Exception as exc:  # a broken verifier is itself a finding, not a crash
        return dict(result, verdict=None, detected=None, reason=f"canary run failed: {exc}", after=None,
                    provenance="unverified")
    verdict = r.get("verdict")
    result.update(verdict=verdict, reason=r.get("reason", ""), after=_counts(r.get("after")),
                  newly_broken_tests=r.get("newly_broken_tests", []))
    if verdict == "could_not_run":
        return dict(result, detected=None, provenance="unverified")
    return dict(result, detected=verdict == EXPECTED, provenance="executed")
