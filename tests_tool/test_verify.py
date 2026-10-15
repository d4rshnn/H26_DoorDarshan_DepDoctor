import copy
import json
import os
from pathlib import Path

import pytest

from tool import verify
from tool.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent
GOOD = json.loads((FIXTURES / "proposals.json").read_text(encoding="utf-8"))
BEFORE = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
FAILING = "tests/test_report.py::test_report_renders_all_sites"

REQUIREMENTS = "toml==0.10.2\nappdirs==1.4.4\nrequests==2.31.0\nJinja2==2.11.3\npydantic==1.10.13\n"
RESOURCES = "import pkg_resources\r\n\r\n\r\ndef template_dir():\r\n    return pkg_resources.resource_filename('x', 'y')\r\n"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "linkwatch").mkdir(parents=True)
    (root / "requirements.txt").write_bytes(REQUIREMENTS.encode())
    (root / "linkwatch" / "resources.py").write_bytes(RESOURCES.encode())  # CRLF on purpose
    (root / "linkwatch" / "dup.py").write_bytes(b"x = 1\nx = 1\n")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "__pycache__").mkdir()
    return root


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def run_result(passed=10, failed=0, failures=(), install=0, skipped=None):
    pytest_part = {"exit_code": 0 if not failed else 1, "passed": passed, "failed": failed, "errors": 0,
                   "failures": [{"test": t, "first_error": "boom"} for t in failures]}
    if skipped:
        pytest_part.update(exit_code=None, passed=None, failed=None, errors=None, skipped=skipped)
    return {"schema": 1, "python": "3.12.2", "install": {"exit_code": install, "output_tail": ""},
            "pytest": pytest_part, "pip_check": "", "provenance": "executed"}


class FakeRun:
    """Stands in for baseline.run: records what it was given and returns a canned result."""
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, path, python, workdir):
        self.calls.append({"path": Path(path), "python": python, "workdir": Path(workdir),
                           "files": snapshot(Path(path))})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def proposal(*changes, pid="p1"):
    return {"id": pid, "problem": "x", "kind": "version_change", "evidence_refs": ["r"],
            "changes": list(changes), "rationale": "x", "risk": "x"}


def verify_one(repo, tmp_path, prop, fake=None):
    fake = fake or FakeRun(run_result())
    out = verify.run(repo, {"proposals": [prop]}, BEFORE, tmp_path / "work", baseline_run=fake)
    return out["results"][0], fake


# ---------------------------------------------------------------- apply

def test_every_action_applies(repo, tmp_path):
    prop = proposal(
        {"file": "requirements.txt", "action": "replace_line", "old": " Jinja2==2.11.3 ", "new": "Jinja2>=3.1,<4"},
        {"file": "requirements.txt", "action": "delete_line", "old": "appdirs==1.4.4"},
        {"file": "linkwatch/resources.py", "action": "replace_text",
         "old": "pkg_resources.resource_filename('x', 'y')", "new": "str(files('x') / 'y')"},
        {"file": "linkwatch/resources.py", "action": "replace_line", "old": "import pkg_resources",
         "new": "from importlib.resources import files"},
        {"file": "linkwatch/new/helper.py", "action": "create_file", "new": "HELP = 1\n"},
    )
    result, fake = verify_one(repo, tmp_path, prop)
    assert result["apply_ok"] is True and result["verdict"] == "improved"
    files = fake.calls[0]["files"]
    assert files["requirements.txt"] == b"toml==0.10.2\nrequests==2.31.0\nJinja2>=3.1,<4\npydantic==1.10.13\n"
    assert files["linkwatch/resources.py"] == (b"from importlib.resources import files\r\n\r\n\r\ndef template_dir():"
                                               b"\r\n    return str(files('x') / 'y')\r\n")  # CRLF kept
    assert files["linkwatch/new/helper.py"] == b"HELP = 1\n"
    assert not any(f.startswith((".git/", "__pycache__/")) for f in files)  # ignored when copying
    assert fake.calls[0]["path"] == tmp_path / "work" / "p1" / "src"
    assert fake.calls[0]["workdir"] == tmp_path / "work" / "p1" / "run"
    assert fake.calls[0]["python"] == "3.12"


def test_diff_is_unified_and_covers_new_files(repo, tmp_path):
    prop = proposal(GOOD["proposals"][0]["changes"][0],
                    {"file": "NOTES.txt", "action": "create_file", "new": "no newline"})
    diff = verify_one(repo, tmp_path, prop)[0]["applied_diff"]
    assert "--- a/requirements.txt\n+++ b/requirements.txt\n" in diff
    assert "-Jinja2==2.11.3\n+Jinja2>=3.1,<4\n" in diff
    assert "--- /dev/null\n+++ b/NOTES.txt\n" in diff and "+no newline\n\\ No newline at end of file\n" in diff


@pytest.mark.parametrize("change,expected", [
    ({"file": "../outside.txt", "action": "create_file", "new": "x"}, "leaves the repo"),
    ({"file": "linkwatch/../../outside.txt", "action": "create_file", "new": "x"}, "leaves the repo"),
    ({"file": "/etc/passwd", "action": "replace_text", "old": "a", "new": "b"}, "absolute path"),
    ({"file": "C:\\evil.txt", "action": "create_file", "new": "x"}, "absolute path"),
    ({"file": "linkwatch/dup.py", "action": "replace_line", "old": "x = 1", "new": "x = 2"}, "found 2 times"),
    ({"file": "linkwatch/dup.py", "action": "replace_text", "old": "x = 1", "new": "x = 2"}, "found 2 times"),
    ({"file": "linkwatch/dup.py", "action": "delete_line", "old": "x = 1"}, "found 2 times"),
    ({"file": "requirements.txt", "action": "replace_line", "old": "Jinja2==9", "new": "x"}, "found 0 times"),
    ({"file": "requirements.txt", "action": "create_file", "new": "x"}, "already exists"),
    ({"file": "missing.py", "action": "replace_text", "old": "a", "new": "b"}, "does not exist"),
    ({"file": "requirements.txt", "action": "chmod", "old": "a"}, "unknown or missing action"),
    ({"file": "requirements.txt", "action": "replace_line", "old": "toml==0.10.2"}, "needs ['new']"),
])
def test_bad_changes_do_not_apply_and_skip_tests(repo, tmp_path, change, expected):
    fake = FakeRun(run_result())
    result, _ = verify_one(repo, tmp_path, proposal(change), fake)
    assert result["apply_ok"] is False and result["verdict"] == "could_not_run"
    assert expected in result["reason"] and result["after"] is None and result["applied_diff"] == ""
    assert fake.calls == []
    assert not (tmp_path / "outside.txt").exists() and not (tmp_path / "work" / "outside.txt").exists()


def test_partial_proposal_writes_nothing(repo, tmp_path):
    prop = proposal(GOOD["proposals"][0]["changes"][0],
                    {"file": "requirements.txt", "action": "delete_line", "old": "not there"})
    result, _ = verify_one(repo, tmp_path, prop)
    assert result["apply_ok"] is False and "change 2" in result["reason"]
    assert (tmp_path / "work" / "p1" / "src" / "requirements.txt").read_text() == REQUIREMENTS


def test_original_repo_is_untouched(repo, tmp_path):
    before = snapshot(repo)
    props = {"proposals": [proposal(GOOD["proposals"][0]["changes"][0], pid="p1"),
                           proposal({"file": "x.txt", "action": "create_file", "new": "x"}, pid="p2"),
                           proposal({"file": "../escape.txt", "action": "create_file", "new": "x"}, pid="p3")]}
    verify.run(repo, props, BEFORE, tmp_path / "work", baseline_run=FakeRun(run_result()))
    assert snapshot(repo) == before


def test_workdir_inside_repo_is_not_copied_into_itself(repo):
    before = snapshot(repo)
    fake = FakeRun(run_result())
    verify.run(repo, {"proposals": [proposal(GOOD["proposals"][0]["changes"][0])]}, BEFORE,
               repo / "sandbox_dir" / "verify", baseline_run=fake)
    assert not any(f.startswith("sandbox_dir/") for f in fake.calls[0]["files"])
    assert {k: v for k, v in snapshot(repo).items() if not k.startswith("sandbox_dir/")} == before


# ---------------------------------------------------------------- verdicts

@pytest.mark.parametrize("after,verdict,fixed,broken,why", [
    (run_result(10, 0), "improved", [FAILING], [], "1 test(s) fixed"),
    (run_result(9, 1, [FAILING]), "unchanged", [], [], "no test changed state"),
    (run_result(9, 1, ["tests/test_cache.py::test_x"]), "regressed", [FAILING], ["tests/test_cache.py::test_x"],
     "newly failing"),
    (run_result(8, 1, [FAILING]), "regressed", [], [], "passed dropped from 9 to 8"),
    (run_result(10, 0, install=1), "regressed", [FAILING], [], "install now fails"),
    (run_result(skipped="pytest is not available in venv"), "could_not_run", [], [], "pytest is not available"),
])
def test_verdicts(repo, tmp_path, after, verdict, fixed, broken, why):
    result, _ = verify_one(repo, tmp_path, GOOD["proposals"][0], FakeRun(after))
    assert result["verdict"] == verdict and why in result["reason"]
    assert result["fixed_tests"] == fixed and result["newly_broken_tests"] == broken
    assert result["after"] == after and result["provenance"] == "executed"


def test_baseline_without_results_is_could_not_run(repo, tmp_path):
    out = verify.run(repo, GOOD, run_result(skipped="Python 3.12 not found"), tmp_path / "w",
                     baseline_run=FakeRun(run_result()))
    assert out["results"][0]["verdict"] == "could_not_run"
    assert "baseline has no test results" in out["results"][0]["reason"]


def test_crashing_test_run_does_not_raise(repo, tmp_path):
    result, _ = verify_one(repo, tmp_path, GOOD["proposals"][0], FakeRun(RuntimeError("disk full")))
    assert result["verdict"] == "could_not_run" and "disk full" in result["reason"]
    assert result["apply_ok"] is True


def test_result_matches_fixture_shape(repo, tmp_path):
    fixture = json.loads((FIXTURES / "verify.json").read_text(encoding="utf-8"))
    out = verify.run(repo, GOOD, BEFORE, tmp_path / "w", baseline_run=FakeRun(run_result()))
    assert set(out) == set(fixture) - {"_fixture"}
    assert set(out["results"][0]) == set(fixture["results"][0]) | {"reason"}


def test_rejected_and_malformed_proposals(repo, tmp_path):
    good = GOOD["proposals"][0]
    props = {"proposals": [good, dict(good, id="p2"), "junk", dict(good, id="../../evil")],
             "rejected": [{"id": "p2", "reason": "x"}]}
    fake = FakeRun(run_result())
    out = verify.run(repo, props, BEFORE, tmp_path / "w", baseline_run=fake)
    assert [r["proposal_id"] for r in out["results"]] == ["p1", None, "../../evil"]
    assert out["results"][1]["verdict"] == "could_not_run"
    assert [c["workdir"].parent.name for c in fake.calls] == ["p1", ".._.._evil"]
    assert all(c["workdir"].parent.parent == tmp_path / "w" for c in fake.calls)


def test_rerun_replaces_old_sandbox(repo, tmp_path):
    prop = GOOD["proposals"][0]
    verify_one(repo, tmp_path, prop)
    result, _ = verify_one(repo, tmp_path, prop)  # would fail "found 0 times" if the old copy were reused
    assert result["apply_ok"] is True


def test_cli_verify(repo, tmp_path, capsys, monkeypatch):
    real_run, fake = verify.run, FakeRun(run_result())
    monkeypatch.setattr(verify, "run", lambda *a, **k: real_run(*a, **k, baseline_run=fake))
    props, base, out = tmp_path / "p.json", tmp_path / "b.json", tmp_path / "v.json"
    props.write_text(json.dumps(GOOD))
    base.write_text(json.dumps(BEFORE))
    assert main(["verify", str(repo), str(props), str(base), "--out", str(out),
                 "--workdir", str(tmp_path / "w")]) == 0
    assert json.loads(out.read_text())["results"][0]["verdict"] == "improved"
    assert "p1: improved" in capsys.readouterr().out
    assert main(["verify", str(tmp_path / "nope"), str(props), str(base), "--out", str(out)]) == 2


# ---------------------------------------------------------------- real run (slow, uses pip and the network)

@pytest.mark.skipif(os.environ.get("DEPDOCTOR_SLOW") != "1", reason="set DEPDOCTOR_SLOW=1 to run")
def test_real_jinja2_fix_on_linkwatch(tmp_path):
    out = verify.run(REPO_ROOT, GOOD, BEFORE, tmp_path / "verify", python="3.12")
    result = out["results"][0]
    print(json.dumps({k: v for k, v in result.items() if k != "after"}, indent=2))
    print(json.dumps(result["after"]["pytest"], indent=2))
    assert result["apply_ok"] is True
    assert result["after"]["pytest"]["passed"] == 10 and result["after"]["pytest"]["failed"] == 0
    assert result["verdict"] == "improved"
    assert result["fixed_tests"] == [FAILING]
