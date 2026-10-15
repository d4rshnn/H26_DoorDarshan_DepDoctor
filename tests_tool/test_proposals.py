import copy
import json
from pathlib import Path

import pytest

from tool import proposals as P
from tool.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = json.loads((FIXTURES / "evidence.json").read_text(encoding="utf-8"))
GOOD = json.loads((FIXTURES / "proposals.json").read_text(encoding="utf-8"))

REQUIREMENTS = "toml==0.10.2\nappdirs==1.4.4\nrequests==2.31.0\nJinja2==2.11.3\npydantic==1.10.13\n"
RESOURCES = "import pkg_resources\n\n\ndef template_dir():\n    return pkg_resources.resource_filename('x', 'y')\n"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "requirements.txt").write_text(REQUIREMENTS)
    pkg = tmp_path / "linkwatch"
    pkg.mkdir()
    (pkg / "report.py").write_text("from datetime import datetime\n\nfrom jinja2 import Environment\n")
    (pkg / "resources.py").write_text(RESOURCES)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    return tmp_path


def one(change=None, **fields):
    """The fixture proposal with fields or its first change overridden."""
    proposal = copy.deepcopy(GOOD["proposals"][0])
    proposal.update(fields)
    if change is not None:
        proposal["changes"] = [change]
    return {"proposals": [proposal]}


def reason(result):
    accepted, rejected = result
    assert accepted == [] and len(rejected) == 1
    return rejected[0]["reason"]


# ---------------------------------------------------------------- validate

def test_fixture_proposal_is_accepted(repo):
    accepted, rejected = P.validate(GOOD, EVIDENCE, repo)
    assert [p["id"] for p in accepted] == ["p1"] and rejected == []


@pytest.mark.skipif(not (REPO_ROOT / "linkwatch").is_dir(), reason="LinkWatch target not present")
def test_fixture_proposal_is_accepted_on_linkwatch():
    accepted, _ = P.validate(GOOD, EVIDENCE, REPO_ROOT)
    assert len(accepted) == 1


def test_json_text_is_accepted_and_garbage_rejected(repo):
    assert len(P.validate(json.dumps(GOOD), EVIDENCE, repo)[0]) == 1
    assert "not valid JSON" in reason(P.validate("Sure! Here is a fix:", EVIDENCE, repo))
    assert "expected an object" in reason(P.validate([1, 2], EVIDENCE, repo))
    assert "expected an object" in reason(P.validate({"proposal": []}, EVIDENCE, repo))


@pytest.mark.parametrize("change,expected", [
    ({"file": "requirements.txt", "action": "rewrite", "old": "a", "new": "b"}, "unknown action"),
    ({"file": "missing.txt", "action": "replace_line", "old": "a", "new": "b"}, "does not exist"),
    ({"file": "../outside.txt", "action": "create_file", "new": "x"}, "leaves the repo"),
    ({"file": "linkwatch/../../x.txt", "action": "create_file", "new": "x"}, "leaves the repo"),
    ({"file": "/etc/passwd", "action": "replace_text", "old": "a", "new": "b"}, "absolute path"),
    ({"file": "C:\\Windows\\x.ini", "action": "replace_text", "old": "a", "new": "b"}, "absolute path"),
    ({"file": "requirements.txt", "action": "replace_line", "old": "Jinja2==9.9", "new": "b"}, "found 0 times"),
    ({"file": "requirements.txt", "action": "replace_line", "old": "Jinja2", "new": "b"}, "found 0 times"),
    ({"file": "requirements.txt", "action": "replace_text", "old": "==", "new": "b"}, "found 5 times"),
    ({"file": "requirements.txt", "action": "delete_line", "old": "six"}, "found 0 times"),
    ({"file": "requirements.txt", "action": "create_file", "new": "x"}, "already exists"),
    ({"file": "requirements.txt", "action": "replace_line", "old": "Jinja2==2.11.3"}, "needs a string 'new'"),
    ({"file": "tests/test_x.py", "action": "replace_text", "old": "True", "new": "1"}, "may not edit tests"),
    ({"file": "requirements.txt", "action": "replace_line", "old": "x", "new": "y", "why": "z"}, "unknown change"),
])
def test_bad_changes_are_rejected(repo, change, expected):
    assert expected in reason(P.validate(one(change), EVIDENCE, repo))


@pytest.mark.parametrize("fields,expected", [
    ({"evidence_refs": []}, "evidence_refs must be a non-empty list"),
    ({"evidence_refs": "usage:linkwatch/report.py:3"}, "evidence_refs must be a non-empty list"),
    ({"evidence_refs": ["usage:linkwatch/report.py:99"]}, "not found in evidence"),
    ({"kind": "rewrite_everything"}, "unknown kind"),
    ({"changes": []}, "changes must be a non-empty list"),
    ({"confidence": "high"}, "unknown fields"),
    ({"risk": ""}, "'risk' must be a non-empty string"),
])
def test_bad_proposals_are_rejected(repo, fields, expected):
    assert expected in reason(P.validate(one(**fields), EVIDENCE, repo))


def test_missing_fields_and_non_object(repo):
    proposal = copy.deepcopy(GOOD["proposals"][0])
    del proposal["rationale"]
    assert "missing fields: ['rationale']" in reason(P.validate({"proposals": [proposal]}, EVIDENCE, repo))
    assert "must be an object" in reason(P.validate({"proposals": ["p1"]}, EVIDENCE, repo))


def test_evidence_none_skips_ref_lookup(repo):
    assert len(P.validate(one(evidence_refs=["anything"]), None, repo)[0]) == 1


def test_good_actions_and_sequential_changes(repo):
    changes = [
        {"file": "linkwatch/resources.py", "action": "replace_line", "old": "import pkg_resources",
         "new": "from importlib.resources import files"},
        {"file": "linkwatch/resources.py", "action": "replace_text",
         "old": "pkg_resources.resource_filename('x', 'y')", "new": "str(files('x') / 'y')"},
        {"file": "requirements.txt", "action": "delete_line", "old": "  appdirs==1.4.4  "},
        {"file": "linkwatch/new_module.py", "action": "create_file", "new": "X = 1\n"},
        {"file": "linkwatch/new_module.py", "action": "replace_text", "old": "X = 1", "new": "X = 2"},
    ]
    accepted, rejected = P.validate(one(kind="api_migration", changes=changes), EVIDENCE, repo)
    assert rejected == [] and len(accepted) == 1
    # the second change must be checked against the result of the first
    stale = [changes[0], {**changes[0], "new": "x"}]
    assert "change 2" in reason(P.validate(one(changes=stale), EVIDENCE, repo))


def test_proposals_are_independent_and_limited(repo):
    p = GOOD["proposals"][0]
    many = {"proposals": [dict(p, id=f"p{i}") for i in range(1, 5)] + [dict(p, id="p1")]}
    accepted, rejected = P.validate(many, EVIDENCE, repo)
    assert [a["id"] for a in accepted] == ["p1", "p2", "p3"]
    assert [r["id"] for r in rejected] == ["p4", "p1"]
    assert "more than 3" in rejected[0]["reason"] and "duplicate id" in rejected[1]["reason"]


def test_apply_change_keeps_indent_and_line_ending():
    text = "def f():\r\n    return old()\r\n"
    change = {"action": "replace_line", "old": "return old()", "new": "return new()"}
    assert P.apply_change(text, change) == ("def f():\r\n    return new()\r\n", None)


# ---------------------------------------------------------------- parser

@pytest.mark.parametrize("reply", [
    json.dumps(GOOD),
    "```json\n" + json.dumps(GOOD) + "\n```",
    "Here you go:\n```\n" + json.dumps(GOOD, indent=2) + "\n```\nHope it helps.",
    "Sure. " + json.dumps(GOOD) + " Done.",
])
def test_parse_reply_accepts_plain_fenced_and_wrapped(reply):
    assert P.parse_reply(reply)["proposals"][0]["id"] == "p1"


def test_parse_reply_rejects_non_json():
    for reply in ("I think you should upgrade Jinja2.", "", None):
        with pytest.raises(ValueError):
            P.parse_reply(reply)


def test_parse_reply_keeps_repeated_proposals_keys():
    # seen from gemma4:e4b: {"proposals": [p1], "proposals": [p2], ...}
    reply = '{"proposals": [{"id": "p1"}], "proposals": [{"id": "p2"}], "note": 1, "note": 2}'
    assert P.parse_reply(reply) == {"proposals": [{"id": "p1"}, {"id": "p2"}], "note": 2}


# ---------------------------------------------------------------- propose with a fake model

class FakeModel:
    def __init__(self, *replies, finish="stop"):
        self.replies, self.calls, self.finish = list(replies), [], finish

    def __call__(self, url, payload):
        self.calls.append((url, copy.deepcopy(payload)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"choices": [{"finish_reason": self.finish, "message": {"role": "assistant", "content": reply}}]}


@pytest.fixture
def evidence_file(repo, tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(dict(EVIDENCE, repo=dict(EVIDENCE["repo"], path=str(repo)))))
    return path


def run(evidence_file, tmp_path, fake, **kwargs):
    out = tmp_path / "proposals.json"
    result = P.propose(evidence_file, out, http=fake, **kwargs)
    assert json.loads(out.read_text(encoding="utf-8")) == result
    return result


def test_propose_first_try(evidence_file, tmp_path):
    fake = FakeModel("```json\n" + json.dumps(GOOD) + "\n```")
    result = run(evidence_file, tmp_path, fake)
    assert [p["id"] for p in result["proposals"]] == ["p1"]
    assert result["attempts"] == 1 and "error" not in result
    assert result["provenance"] == "ai_generated" and result["model"] == "gemma4:e4b"
    url, payload = fake.calls[0]
    assert url == "http://localhost:11434/v1/chat/completions"
    assert payload["model"] == "gemma4:e4b" and payload["stream"] is False
    assert payload["reasoning_effort"] == "none"
    prompt = payload["messages"][1]["content"]
    assert "[manifest:requirements.txt:4] Jinja2==2.11.3" in prompt
    assert "[candidate:c2] HINT Jinja2" in prompt
    assert "--- requirements.txt\ntoml==0.10.2" in prompt


def test_propose_retries_with_validator_message(evidence_file, tmp_path):
    bad = one({"file": "requirements.txt", "action": "replace_line", "old": "Jinja2==9", "new": "x"})
    fake = FakeModel(json.dumps(bad), json.dumps(GOOD))
    result = run(evidence_file, tmp_path, fake)
    assert result["attempts"] == 2 and len(result["proposals"]) == 1 and "error" not in result
    assert result["rejected"][0]["attempt"] == 1
    first, retry = fake.calls[0][1]["messages"], fake.calls[1][1]["messages"]
    assert retry[:2] == first and len(retry) == 3  # original prompt + reasons, to fit a small context
    assert "rejected" in retry[-1]["content"] and "found 0 times" in retry[-1]["content"]


def test_propose_gives_up_after_one_retry(evidence_file, tmp_path):
    fake = FakeModel("no idea", "still no idea", "never asked")
    result = run(evidence_file, tmp_path, fake)
    assert result["proposals"] == [] and result["attempts"] == 2 and len(fake.calls) == 2
    assert "no usable proposal after 2 attempt(s)" in result["error"]
    assert result["rejected"][1]["raw"] == "still no idea"


def test_propose_with_baseline_puts_failures_in_prompt(evidence_file, tmp_path):
    fake = FakeModel(json.dumps(GOOD))
    result = run(evidence_file, tmp_path, fake, baseline_path=FIXTURES / "baseline.json")
    assert [p["id"] for p in result["proposals"]] == ["p1"]
    assert result["baseline_failures"] == ["tests/test_report.py::test_report_renders_all_sites"]
    prompt = fake.calls[0][1]["messages"][1]["content"]
    assert ("- tests/test_report.py::test_report_renders_all_sites: "
            "ImportError: cannot import name 'soft_unicode' from 'markupsafe'") in prompt
    assert P.BASELINE_INSTRUCTION in prompt
    assert prompt.index("FAILING TESTS") < prompt.index("EVIDENCE")


def test_propose_without_baseline_has_no_baseline_section(evidence_file, tmp_path):
    fake = FakeModel(json.dumps(GOOD))
    result = run(evidence_file, tmp_path, fake)
    assert [p["id"] for p in result["proposals"]] == ["p1"] and "baseline_failures" not in result
    prompt = fake.calls[0][1]["messages"][1]["content"]
    assert "FAILING TESTS" not in prompt and P.BASELINE_INSTRUCTION not in prompt
    assert prompt.startswith("EVIDENCE")


def test_baseline_without_failures(evidence_file, tmp_path):
    passing = tmp_path / "baseline.json"
    passing.write_text(json.dumps({"schema": 1, "pytest": {"exit_code": 0, "passed": 10, "failures": []}}))
    fake = FakeModel(json.dumps(GOOD))
    result = run(evidence_file, tmp_path, fake, baseline_path=passing)
    assert result["baseline_failures"] == []
    assert "no failing tests" in fake.calls[0][1]["messages"][1]["content"]


def test_cli_propose_accepts_baseline_flag():
    from tool.cli import build_parser
    args = build_parser().parse_args(["propose", "evidence.json", "--baseline", "baseline.json"])
    assert args.baseline == "baseline.json"
    assert build_parser().parse_args(["propose", "evidence.json"]).baseline is None


def test_propose_explains_cut_off_reply(evidence_file, tmp_path):
    result = run(evidence_file, tmp_path, FakeModel("", "", finish="length"))
    assert "ran out of context" in result["error"]


def test_propose_records_unreachable_model(evidence_file, tmp_path):
    result = run(evidence_file, tmp_path, FakeModel(OSError("connection refused")))
    assert result["proposals"] == [] and "connection refused" in result["error"]


@pytest.mark.parametrize("url", ["https://api.example.com/v1", "http://192.168.1.5:11434/v1",
                                 "http://localhost.evil.com/v1", "file:///etc/passwd"])
def test_propose_refuses_non_local_endpoints(evidence_file, tmp_path, url):
    fake = FakeModel(json.dumps(GOOD))
    with pytest.raises(ValueError, match="non-local"):
        P.propose(evidence_file, tmp_path / "out.json", base_url=url, http=fake)
    assert fake.calls == []
    with pytest.raises(ValueError, match="non-local"):
        P.http_post_json(url + "/chat/completions", {})


# ---------------------------------------------------------------- cli

def test_cli_check_proposals(repo, evidence_file, tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(GOOD))
    assert main(["check-proposals", str(good), "--evidence", str(evidence_file)]) == 0
    assert "accepted p1" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["check-proposals", str(bad), "--evidence", str(evidence_file), "--repo", str(repo)]) == 1
    assert "not valid JSON" in capsys.readouterr().out


def test_cli_propose_refuses_remote_url(evidence_file, tmp_path, capsys):
    assert main(["propose", str(evidence_file), "--out", str(tmp_path / "p.json"),
                 "--base-url", "https://api.example.com/v1"]) == 2
    assert "non-local" in capsys.readouterr().err
