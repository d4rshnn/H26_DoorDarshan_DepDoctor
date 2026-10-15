import copy
import json
from pathlib import Path

import pytest

from tool import canary as C
from tool import verify

FIXTURES = Path(__file__).parent / "fixtures"
BASELINE = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))

REQUIREMENTS = """\
toml==0.10.2
appdirs==1.4.4
requests==2.31.0
Jinja2==2.11.3
pydantic==1.10.13
click>=7
six
idna==2.7
rich
pytest
"""


def usage(package, file, imp=None):
    return {"package": package, "import": imp or package.lower(), "file": file, "line": 1, "names": [],
            "provenance": "observed"}


def evidence_for(text, used):
    """Evidence with a manifest entry per requirements line and the given usage entries."""
    manifests = []
    for n, line in enumerate(text.splitlines(), 1):
        name = line.split("==")[0].split(">=")[0].split("[")[0].strip()
        if name and not name.startswith("#"):
            manifests.append({"file": "requirements.txt", "package": name, "specifier": "", "line": n,
                              "provenance": "observed"})
    return {"manifests": manifests, "usage": used}


LINKWATCH_USAGE = [usage("click", "linkwatch/cli.py"), usage("rich", "linkwatch/cli.py", "rich.console"),
                   usage("requests", "linkwatch/checker.py"), usage("toml", "linkwatch/config.py"),
                   usage("Jinja2", "linkwatch/report.py", "jinja2"), usage("pytest", "tests/conftest.py")]


@pytest.fixture
def repo(tmp_path):
    def make(text=REQUIREMENTS):
        (tmp_path / "requirements.txt").write_text(text)
        return tmp_path
    return make


# ---------------------------------------------------------------- choice

def test_prefers_click(repo):
    root = repo()
    proposal = C.build_canary(evidence_for(REQUIREMENTS, LINKWATCH_USAGE), root)
    assert proposal["id"] == "canary" and proposal["kind"] == "remove_unused"
    assert proposal["changes"] == [{"file": "requirements.txt", "action": "delete_line", "old": "click>=7"}]
    assert proposal["evidence_refs"] == ["manifest:requirements.txt:6"]
    assert proposal["rationale"].startswith("negative control: removes a package the code imports")
    assert proposal["risk"] == "deliberately harmful; never applied to the target"


@pytest.mark.parametrize("drop,expected", [
    ({"click"}, "rich"),
    ({"click", "rich"}, "requests==2.31.0"),
    ({"click", "rich", "requests"}, "toml==0.10.2"),  # first usable in file order
])
def test_preference_order_then_first_usable(repo, drop, expected):
    used = [u for u in LINKWATCH_USAGE if u["package"] not in drop]
    proposal = C.build_canary(evidence_for(REQUIREMENTS, used), repo())
    assert proposal["changes"][0]["old"] == expected


def test_only_test_files_import_it_is_skipped(repo):
    used = [usage("click", "tests/test_cli.py"), usage("rich", "linkwatch/conftest.py", "rich"),
            usage("toml", "linkwatch/config.py")]
    assert C.build_canary(evidence_for(REQUIREMENTS, used), repo())["changes"][0]["old"] == "toml==0.10.2"


def test_packages_with_declared_dependants_and_tools_are_skipped(repo):
    # idna is pulled in by requests, so deleting it would break nothing; pytest is the test runner
    used = [usage("idna", "linkwatch/x.py"), usage("pytest", "linkwatch/y.py"), usage("six", "linkwatch/z.py")]
    text = "requests==2.31.0\nidna==2.7\npytest\nsix\n"
    assert C.build_canary(evidence_for(text, used), repo(text))["changes"][0]["old"] == "six"
    text = "requests==2.31.0\nidna==2.7\npytest\n"
    assert C.build_canary(evidence_for(text, used), repo(text)) is None


@pytest.mark.parametrize("line", ["click[extra]>=7", "click>=7 ; python_version < '3.13'", "click>=7,<9",
                                  "click @ file:///x", "click>=7  # cli"])
def test_only_plain_lines_qualify(repo, line):
    text = f"{line}\n"
    assert C.build_canary(evidence_for(text, [usage("click", "linkwatch/cli.py")]), repo(text)) is None


def test_no_requirements_file(tmp_path):
    assert C.build_canary(evidence_for(REQUIREMENTS, LINKWATCH_USAGE), tmp_path) is None


# ---------------------------------------------------------------- run

def after(passed, failed=0, errors=0):
    return dict(BASELINE, pytest={"exit_code": 1, "passed": passed, "failed": failed, "errors": errors,
                                  "failures": []})


def fake_verify(verdict, after_run=None, error=None):
    calls = []

    def run(repo, proposals, baseline, workdir, python="3.12"):
        calls.append({"ids": [p["id"] for p in proposals["proposals"]], "workdir": workdir, "python": python,
                      "proposals": proposals})
        if error:
            raise error
        return {"schema": 1, "results": [{"proposal_id": "canary", "verdict": verdict, "reason": f"because {verdict}",
                                          "after": after_run, "newly_broken_tests": [], "apply_ok": True}]}
    run.calls = calls
    return run


def test_detected(repo, tmp_path):
    fake = fake_verify("regressed", after(0, errors=1))
    result = C.run_canary(repo(), evidence_for(REQUIREMENTS, LINKWATCH_USAGE), BASELINE, tmp_path / "w", "3.12",
                          verify_run=fake)
    assert result["detected"] is True and result["verdict"] == "regressed" and result["expected"] == "regressed"
    assert result["package"] == "click" and result["line_removed"] == "click>=7"
    assert result["provenance"] == "executed" and result["reason"] == "because regressed"
    assert result["before"]["passed"] == 9 and result["after"] == {"exit_code": 1, "passed": 0, "failed": 0, "errors": 1}
    assert fake.calls[0]["ids"] == ["canary"] and fake.calls[0]["workdir"] == tmp_path / "w"


@pytest.mark.parametrize("verdict", ["improved", "unchanged"])
def test_not_detected(repo, tmp_path, verdict):
    result = C.run_canary(repo(), evidence_for(REQUIREMENTS, LINKWATCH_USAGE), BASELINE, tmp_path / "w",
                          verify_run=fake_verify(verdict, after(9, 1)))
    assert result["detected"] is False and result["verdict"] == verdict and result["provenance"] == "executed"


def test_could_not_run_is_not_judged(repo, tmp_path):
    result = C.run_canary(repo(), evidence_for(REQUIREMENTS, LINKWATCH_USAGE), BASELINE, tmp_path / "w",
                          verify_run=fake_verify("could_not_run"))
    assert result["detected"] is None and result["provenance"] == "unverified"


def test_verifier_crash_is_not_judged(repo, tmp_path):
    result = C.run_canary(repo(), evidence_for(REQUIREMENTS, LINKWATCH_USAGE), BASELINE, tmp_path / "w",
                          verify_run=fake_verify("x", error=RuntimeError("boom")))
    assert result["detected"] is None and "boom" in result["reason"]


def test_no_suitable_package(tmp_path):
    fake = fake_verify("regressed")
    result = C.run_canary(tmp_path, {"manifests": [], "usage": []}, BASELINE, tmp_path / "w", verify_run=fake)
    assert result["detected"] is None and result["reason"] == "no suitable package"
    assert result["provenance"] == "unverified" and fake.calls == []


def test_real_verify_with_fake_tests_leaves_repo_untouched(repo, tmp_path):
    root = repo()
    before = root.joinpath("requirements.txt").read_bytes()
    seen = {}

    def tests_fail(path, python, workdir):
        seen["requirements"] = Path(path, "requirements.txt").read_text()
        return after(0, errors=1)

    def real(repo_, proposals, baseline, workdir, python="3.12"):
        return verify.run(repo_, proposals, baseline, workdir, python, baseline_run=tests_fail)

    result = C.run_canary(root, evidence_for(REQUIREMENTS, LINKWATCH_USAGE), BASELINE, tmp_path / "sandbox",
                          verify_run=real)
    assert result["detected"] is True
    assert "click" not in seen["requirements"].splitlines() and "click>=7" not in seen["requirements"]
    assert root.joinpath("requirements.txt").read_bytes() == before
