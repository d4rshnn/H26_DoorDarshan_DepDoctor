import copy
import json
import os
import urllib.request
from pathlib import Path

import pytest

from tool import finish as F
from tool import verify
from tool.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
EVIDENCE = json.loads((FIXTURES / "evidence.json").read_text(encoding="utf-8"))
BASELINE = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
GOOD = json.loads((FIXTURES / "proposals.json").read_text(encoding="utf-8"))
FAILING = "tests/test_report.py::test_report_renders_all_sites"
OUTPUTS = ("evidence.json", "baseline.json", "verify.json", "capsule.json", "capsule.md", "capsule.html", "run.json")


def after_run(passed=10, failed=0):
    return dict(BASELINE, pytest={"exit_code": 0, "passed": passed, "failed": failed, "errors": 0, "failures": []})


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "target"
    (root / "linkwatch").mkdir(parents=True)
    (root / "requirements.txt").write_text("toml==0.10.2\nappdirs==1.4.4\nrequests==2.31.0\nJinja2==2.11.3\n")
    (root / "linkwatch" / "report.py").write_text("from datetime import datetime\n\nfrom jinja2 import Environment\n")
    return root


class Fakes:
    """Stand-ins for every stage, recording how they were called."""

    def __init__(self, propose_result=None, propose_error=None):
        self.calls = {"scan": [], "baseline": [], "propose": [], "verify": []}
        self.propose_result = propose_result if propose_result is not None else dict(
            copy.deepcopy(GOOD), model="gemma4:e4b", rejected=[], attempts=1)
        self.propose_error = propose_error

    def scan(self, repo):
        self.calls["scan"].append(repo)
        if not Path(repo).is_dir():
            raise FileNotFoundError(f"not a directory: {repo}")
        return dict(copy.deepcopy(EVIDENCE), repo=dict(EVIDENCE["repo"], path=str(repo)))

    def baseline(self, repo, python="3.12", workdir=None):
        self.calls["baseline"].append({"repo": repo, "python": python, "workdir": Path(workdir)})
        return copy.deepcopy(BASELINE)

    def propose(self, evidence_path, out_path, **kwargs):
        self.calls["propose"].append({"evidence": Path(evidence_path), "out": Path(out_path), **kwargs})
        if self.propose_error:
            raise self.propose_error
        Path(out_path).write_text(json.dumps(self.propose_result))
        return copy.deepcopy(self.propose_result)

    def verify(self, repo, proposals, baseline, workdir, python="3.12"):
        self.calls["verify"].append({"ids": [p["id"] for p in proposals["proposals"]], "workdir": Path(workdir)})
        return {"schema": 1, "results": [{
            "proposal_id": p["id"], "applied_diff": "--- a/requirements.txt\n+++ b/requirements.txt\n",
            "apply_ok": True, "after": after_run(), "fixed_tests": [FAILING], "newly_broken_tests": [],
            "verdict": "improved", "reason": "1 test(s) fixed", "provenance": "executed"}
            for p in proposals["proposals"]]}

    def kwargs(self):
        return {"scan_fn": self.scan, "baseline_fn": self.baseline, "propose_fn": self.propose,
                "verify_fn": self.verify}


def run_finish(repo, out, fakes, **kw):
    return F.finish(repo, out, **fakes.kwargs(), **kw)


def capsule_text(out):
    return json.dumps(json.loads((out / "capsule.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- analyze

def test_analyze_writes_files_and_prints_facts(repo, tmp_path, capsys):
    fakes, out = Fakes(), tmp_path / "out"
    result = F.analyze(repo, out, scan_fn=fakes.scan, baseline_fn=fakes.baseline)
    assert json.loads((out / "evidence.json").read_text())["repo"]["path"] == str(repo)
    assert json.loads((out / "baseline.json").read_text())["pytest"]["failed"] == 1
    assert fakes.calls["baseline"][0]["workdir"] == out / ".sandbox" / "baseline"
    printed = capsys.readouterr().out
    assert f"target: {repo.resolve()}" in printed
    assert "tests: 9 passed, 1 failed, 0 errors" in printed
    assert f"FAILING {FAILING}: ImportError: cannot import name 'soft_unicode'" in printed
    for ref in ("manifest:requirements.txt:4", "usage:linkwatch/report.py:3", "drift:pydantic", "candidate:c1"):
        assert f"  {ref}\n" in printed and ref in result["refs"]


def test_analyze_missing_repo_does_not_raise(tmp_path, capsys):
    fakes = Fakes()
    result = F.analyze(tmp_path / "nope", tmp_path / "out", scan_fn=fakes.scan, baseline_fn=fakes.baseline)
    assert result["evidence"] is None and result["baseline"] is None and result["refs"] == []
    assert fakes.calls["baseline"] == []
    assert "scan failed" in capsys.readouterr().out


# ---------------------------------------------------------------- finish

def test_agent_proposals_are_used_when_valid(repo, tmp_path, capsys):
    fakes, out = Fakes(), tmp_path / "out"
    out.mkdir()
    (out / "proposals.json").write_text(json.dumps(GOOD))  # no "model" field, like a hand/agent-written file
    run = run_finish(repo, out, fakes)
    assert run["origin"] == F.ORIGIN_AGENT and fakes.calls["propose"] == []
    assert fakes.calls["scan"] and fakes.calls["baseline"]  # analyze ran because outputs were missing
    assert fakes.calls["verify"][0]["ids"] == ["p1"]
    assert fakes.calls["verify"][0]["workdir"] == out / ".sandbox" / "verify"
    for name in OUTPUTS:
        assert (out / name).is_file(), name
    capsule = json.loads((out / "capsule.json").read_text(encoding="utf-8"))
    assert capsule["model"]["name"] == "gemma4:e4b" and capsule["model"]["agent"].startswith("Hermes")
    assert run["model"]["name_source"].startswith("configured model")
    assert "Proposal origin: agent-written." in capsule_text(out)
    assert "Dependency Exit Capsule" in (out / "capsule.html").read_text(encoding="utf-8")
    assert [s["step"] for s in run["steps"]] == ["scan", "baseline", "verify", "canary", "strength", "capsule",
                                                 "write capsule", "report"]
    assert all(s["ok"] and s["seconds"] >= 0 and s["command"] for s in run["steps"])
    assert run["python"]["requested"] == "3.12" and run["python"]["baseline"] == "3.12.x"
    printed = capsys.readouterr().out
    assert "p1: improved (before 9 passed, 1 failed, 0 errors; after 10 passed, 0 failed, 0 errors)" in printed
    assert str(out / "capsule.html") in printed


def test_existing_evidence_and_baseline_are_reused(repo, tmp_path):
    fakes, out = Fakes(), tmp_path / "out"
    F.analyze(repo, out, scan_fn=fakes.scan, baseline_fn=fakes.baseline)
    (out / "proposals.json").write_text(json.dumps(GOOD))
    fresh = Fakes()
    run_finish(repo, out, fresh)
    assert fresh.calls["scan"] == [] and fresh.calls["baseline"] == []


def test_fallback_when_proposals_json_missing(repo, tmp_path):
    fakes, out = Fakes(), tmp_path / "out"
    run = run_finish(repo, out, fakes, model="gemma4:e4b")
    assert run["origin"] == F.ORIGIN_FALLBACK
    call = fakes.calls["propose"][0]
    assert call["evidence"] == out / "evidence.json" and call["out"] == out / "proposals.fallback.json"
    assert call["baseline_path"] == out / "baseline.json" and call["repo_path"] == repo
    assert call["model"] == "gemma4:e4b" and call["base_url"].startswith("http://localhost")
    assert run["outputs"]["proposals"] == str(out / "proposals.fallback.json")
    assert run["model"]["agent"].startswith("none") and fakes.calls["verify"][0]["ids"] == ["p1"]
    assert "Proposal origin: fallback: direct local-model call." in capsule_text(out)


def test_fallback_when_agent_proposals_invalid(repo, tmp_path):
    fakes, out = Fakes(), tmp_path / "out"
    out.mkdir()
    bad = copy.deepcopy(GOOD)
    bad["proposals"][0]["changes"][0]["old"] = "Jinja2==9.9.9"
    (out / "proposals.json").write_text(json.dumps(bad))
    run = run_finish(repo, out, fakes)
    assert run["origin"] == F.ORIGIN_FALLBACK and len(fakes.calls["propose"]) == 1
    assert json.loads((out / "proposals.json").read_text()) == bad  # the agent's file is kept as written
    assert "agent-written proposals.json" in capsule_text(out) and "found 0 times" in capsule_text(out)


def test_garbage_proposals_json_falls_back(repo, tmp_path):
    fakes, out = Fakes(), tmp_path / "out"
    out.mkdir()
    (out / "proposals.json").write_text("I could not decide.")
    assert run_finish(repo, out, fakes)["origin"] == F.ORIGIN_FALLBACK


def test_no_proposal_still_writes_capsule(repo, tmp_path, capsys):
    fakes = Fakes(propose_result={"schema": 1, "model": "gemma4:e4b", "proposals": [], "rejected": [],
                                  "attempts": 2, "error": "could not get a reply"})
    out = tmp_path / "out"
    run = run_finish(repo, out, fakes)
    assert fakes.calls["verify"] == []
    for name in OUTPUTS:
        assert (out / name).is_file(), name
    text = capsule_text(out)
    assert "No usable proposal: nothing was applied or verified." in text and '"unverified"' in text
    assert json.loads((out / "verify.json").read_text()) == {"schema": 1, "results": []}
    assert "no proposal was verified" in capsys.readouterr().out
    assert run["origin"] == F.ORIGIN_FALLBACK


def test_model_down_is_recorded_not_raised(repo, tmp_path):
    fakes, out = Fakes(propose_error=OSError("connection refused")), tmp_path / "out"
    run = run_finish(repo, out, fakes)
    step = next(s for s in run["steps"] if s["step"] == "propose")
    assert step["ok"] is False and "connection refused" in step["error"]
    assert (out / "capsule.md").is_file() and "No usable proposal" in capsule_text(out)


def test_missing_repo_still_writes_capsule(tmp_path):
    fakes, out = Fakes(), tmp_path / "out"
    run = run_finish(tmp_path / "nope", out, fakes)
    assert run["origin"] == F.ORIGIN_NONE and (out / "capsule.json").is_file()
    assert "Input evidence is missing" in capsule_text(out)


def test_target_folder_untouched(repo, tmp_path):
    from tool import scan
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    fakes, out = Fakes(), tmp_path / "out"

    def real_verify_fake_tests(repo_, proposals, baseline, workdir, python="3.12"):
        return verify.run(repo_, proposals, baseline, workdir, python, baseline_run=lambda *a: after_run())

    F.finish(repo, out, scan_fn=scan.scan, baseline_fn=fakes.baseline, propose_fn=fakes.propose,
             verify_fn=real_verify_fake_tests)
    assert {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()} == before
    result = json.loads((out / "verify.json").read_text())["results"][0]
    assert result["apply_ok"] is True and "+Jinja2>=3.1,<4" in result["applied_diff"]
    assert (out / ".sandbox" / "verify" / "p1" / "src" / "requirements.txt").read_text().splitlines()[3] == \
        "Jinja2>=3.1,<4"


# ---------------------------------------------------------------- canary

class CanaryFakes(Fakes):
    """Like Fakes, but the canary (and only the canary) comes back regressed, as a working verifier would."""

    def verify(self, repo, proposals, baseline, workdir, python="3.12"):
        out = super().verify(repo, proposals, baseline, workdir, python)
        for r in out["results"]:
            if r["proposal_id"] == "canary":
                r.update(verdict="regressed", reason="passed dropped from 9 to 0", fixed_tests=[],
                         after=after_run(0, 0), newly_broken_tests=[FAILING])
        return out


def test_finish_runs_canary_and_passes_it_on(repo, tmp_path, capsys):
    fakes, out = CanaryFakes(), tmp_path / "out"
    run = run_finish(repo, out, fakes)
    assert [c["ids"] for c in fakes.calls["verify"]] == [["p1"], ["canary"]]
    assert fakes.calls["verify"][1]["workdir"] == out / ".sandbox" / "canary"
    canary = json.loads((out / "canary.json").read_text())
    assert canary["detected"] is True and canary["package"] == "toml" and canary["verdict"] == "regressed"
    assert run["canary"] == {"package": "toml", "verdict": "regressed", "expected": "regressed", "detected": True,
                             "reason": "passed dropped from 9 to 0"}
    assert run["outputs"]["canary"] == str(out / "canary.json")
    assert "canary: removed toml, verdict regressed, detected yes" in capsys.readouterr().out
    text = capsule_text(out)
    assert "Verifier self-check (negative control): removed toml" in text
    assert "the verifier detected the breakage: yes" in text


def test_canary_is_never_a_proposal_or_a_fix(repo, tmp_path):
    fakes, out = CanaryFakes(), tmp_path / "out"
    run_finish(repo, out, fakes)
    for name in ("verify.json", "proposals.fallback.json"):
        assert "canary" not in (out / name).read_text(), name
    capsule = json.loads((out / "capsule.json").read_text(encoding="utf-8"))
    sec5_6 = json.dumps([s for s in capsule["sections"] if s["id"] in ("5", "6")])
    assert "canary" not in sec5_6.lower()
    assert "- canary:" not in (out / "capsule.md").read_text(encoding="utf-8").split("## Sections")[0]


def test_missed_canary_is_flagged(repo, tmp_path, capsys):
    fakes, out = Fakes(), tmp_path / "out"  # plain Fakes says "improved" to everything: a broken verifier
    run_finish(repo, out, fakes)
    assert "detected NO" in capsys.readouterr().out
    capsule = json.loads((out / "capsule.json").read_text(encoding="utf-8"))
    sec9 = [i for i in capsule["sections"][8]["items"] if i["source"] == "canary.json"]
    sec10 = [i for i in capsule["sections"][9]["items"] if i["source"] == "canary.json"]
    assert sec9 and sec9[0]["text"].startswith("WARNING: the verifier missed a known-bad change")
    assert sec10 and sec10[0]["provenance"] == "unverified"


def test_no_canary_skips_it(repo, tmp_path, capsys):
    fakes, out = CanaryFakes(), tmp_path / "out"
    run = run_finish(repo, out, fakes, canary=False)
    assert [c["ids"] for c in fakes.calls["verify"]] == [["p1"]]
    assert run["canary"]["detected"] is None and "--no-canary" in run["canary"]["reason"]
    assert "canary" not in [s["step"] for s in run["steps"]]
    assert "canary: not judged (skipped: --no-canary was given)" in capsys.readouterr().out
    assert "Verifier self-check (negative control) was not judged" in capsule_text(out)
    from tool.cli import build_parser
    assert build_parser().parse_args(["finish", "r", "--no-canary"]).no_canary is True
    assert build_parser().parse_args(["finish", "r"]).no_canary is False


def test_canary_skipped_when_nothing_verified(repo, tmp_path):
    fakes = Fakes(propose_result={"schema": 1, "model": "m", "proposals": [], "rejected": [], "error": "x"})
    out = tmp_path / "out"
    run = run_finish(repo, out, fakes)
    assert fakes.calls["verify"] == [] and "no proposal was verified" in run["canary"]["reason"]
    assert json.loads((out / "canary.json").read_text())["provenance"] == "unverified"


# ---------------------------------------------------------------- strength

def test_finish_writes_strength_and_passes_it_on(repo, tmp_path, capsys):
    (repo / "tests").mkdir()
    (repo / "tests" / "test_report.py").write_text("from linkwatch import report\n")
    fakes, out = CanaryFakes(), tmp_path / "out"
    run = run_finish(repo, out, fakes)
    strength = json.loads((out / "strength.json").read_text())
    assert strength["provenance"] == "observed"
    entry = strength["proposals"][0]
    assert entry["proposal_id"] == "p1" and entry["packages"] == ["Jinja2"]
    assert entry["reach"][0]["chain"] == "tests/test_report.py -> linkwatch/report.py -> jinja2"
    assert run["outputs"]["strength"] == str(out / "strength.json")
    assert "strength p1: Jinja2: 1 test file(s), e.g. tests/test_report.py -> linkwatch/report.py -> jinja2" \
        in capsys.readouterr().out
    assert "Evidence strength for p1: 1 test file(s) reach Jinja2" in capsule_text(out)


def test_finish_strength_without_reaching_test(repo, tmp_path):
    fakes, out = CanaryFakes(), tmp_path / "out"  # the fixture repo has no tests at all
    run_finish(repo, out, fakes)
    text = capsule_text(out)
    assert "Evidence strength for p1: no test reaches Jinja2: the test result above cannot confirm this change" in text
    assert "No test imports the code changed by p1 (Jinja2)" in text


def test_strength_empty_when_nothing_verified(repo, tmp_path):
    fakes = Fakes(propose_result={"schema": 1, "model": "m", "proposals": [], "rejected": [], "error": "x"})
    out = tmp_path / "out"
    run_finish(repo, out, fakes)
    assert json.loads((out / "strength.json").read_text())["proposals"] == []
    assert "Evidence strength" not in capsule_text(out)


# ---------------------------------------------------------------- html page

def test_finish_page_uses_stage_data(repo, tmp_path):
    fakes, out = CanaryFakes(), tmp_path / "out"
    run = run_finish(repo, out, fakes)
    page = (out / "capsule.html").read_text(encoding="utf-8")
    assert "Target as it is today" in page and "With the verified fix" in page
    assert '<span class="verdict tone-good">✓ Caught</span>' in page
    assert f"Run time: <b>{run['total_seconds']} s</b>" in page
    assert "Proposals: <b>fallback: direct local-model call</b>" in page
    timeline = page[page.index('id="timeline"'):page.index('id="findings"')]
    assert "<b>verify</b>" in timeline and "<b>self-check</b>" in timeline
    assert run["steps"][-1]["step"] == "report" and run["outputs"]["capsule_html"] == str(out / "capsule.html")
    on_disk = json.loads((out / "run.json").read_text())
    assert on_disk["steps"][-1]["step"] == "report" and "capsule_html" in on_disk["outputs"]


# ---------------------------------------------------------------- cli

def test_cli_capsule_and_report(tmp_path, capsys):
    args = ["capsule", "--out-dir", str(tmp_path), "--model", "gemma4:e4b"]
    for name in ("evidence", "baseline", "proposals", "verify"):
        args += [f"--{name}", str(FIXTURES / f"{name}.json")]
    assert main(args) == 0
    assert json.loads((tmp_path / "capsule.json").read_text(encoding="utf-8"))["model"]["name"] == "gemma4:e4b"
    assert main(["report", str(tmp_path / "capsule.json"), "--out", str(tmp_path / "c.html")]) == 0
    assert (tmp_path / "c.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert main(["report", str(tmp_path / "missing.json")]) == 2


def test_cli_analyze_and_finish_arguments():
    from tool.cli import build_parser
    a = build_parser().parse_args(["analyze", "repo", "--out-dir", "o"])
    assert (a.repo, a.out_dir, a.python) == ("repo", "o", "3.12")
    f = build_parser().parse_args(["finish", "repo", "--out-dir", "o", "--python", "3.13"])
    assert (f.out_dir, f.python, f.model) == ("o", "3.13", "gemma4:e4b")


# ---------------------------------------------------------------- real run (slow: venvs, pip, local model)

TARGET = Path(os.environ.get("DEPDOCTOR_TARGET", r"D:\Hackathons\LinkWatch_target"))


def _ollama_up():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://localhost:11434/api/tags", timeout=3):
            return True
    except OSError:
        return False


@pytest.mark.skipif(os.environ.get("DEPDOCTOR_SLOW") != "1", reason="set DEPDOCTOR_SLOW=1 to run")
def test_real_finish_on_linkwatch(tmp_path):
    if not TARGET.is_dir():
        pytest.skip(f"target folder not found: {TARGET} (set DEPDOCTOR_TARGET)")
    if not _ollama_up():
        pytest.skip("Ollama is not running on localhost:11434")
    out = Path(os.environ.get("DEPDOCTOR_OUT", tmp_path / "out"))
    before = {p: p.read_bytes() for p in TARGET.rglob("*") if p.is_file() and ".sandbox" not in p.parts}
    run = F.finish(TARGET, out)
    print(json.dumps(run, indent=2))
    print((out / "capsule.md").read_text(encoding="utf-8"))
    for name in OUTPUTS:
        assert (out / name).is_file(), name
    assert run["python"]["baseline"].startswith("3.12")
    assert {p: p.read_bytes() for p in TARGET.rglob("*") if p.is_file() and ".sandbox" not in p.parts} == before
