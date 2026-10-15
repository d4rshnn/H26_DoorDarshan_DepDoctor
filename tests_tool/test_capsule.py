import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tool.capsule import build, load_json, render_markdown, validate_capsule, write

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return load_json(FIXTURES / f"{name}.json")


@pytest.fixture
def inputs():
    return tuple(fixture(n) for n in ("evidence", "baseline", "proposals", "verify"))


def test_full_build_shape(inputs):
    capsule = build(*inputs, now="2026-10-15T00:00:00Z")
    assert len(capsule["sections"]) == 10
    assert [s["id"] for s in capsule["sections"]] == [str(i) for i in range(1, 11)]
    assert all(set(item) >= {"text", "provenance", "source"} for s in capsule["sections"] for item in s["items"])
    assert validate_capsule(capsule) == []


def test_provenance_honesty(inputs):
    capsule = build(*inputs)
    candidates = [i for i in capsule["sections"][2]["items"] if "Hint (not verified):" in i["text"]]
    assert candidates and all(i["provenance"] == "hint" for i in candidates)
    proposals = [i for s in capsule["sections"] for i in s["items"] if i["source"].startswith("proposals.json#proposals")]
    assert proposals and all(i["provenance"] == "ai_generated" for i in proposals)
    assert any(i["provenance"] == "executed" and "9 passed" in i["text"] for i in capsule["sections"][0]["items"])
    assert any(i["provenance"] == "executed" and "10 passed" in i["text"] for i in capsule["sections"][7]["items"])


@pytest.mark.parametrize("missing", [0, 1, 2, 3])
def test_missing_each_input(inputs, missing):
    data = list(inputs); data[missing] = None
    capsule = build(*data)
    assert len(capsule["sections"]) == 10
    assert any(i["provenance"] == "unverified" and "missing" in i["text"].lower() for i in capsule["sections"][9]["items"])


def test_missing_inputs_all_and_combinations(inputs):
    assert len(build(inputs[0], None, None, None)["sections"]) == 10
    for mask in range(16):
        data = tuple(v if mask & (1 << i) else None for i, v in enumerate(inputs))
        assert len(build(*data)["sections"]) == 10


def test_regressed_result_in_sections_8_and_9(inputs):
    evidence, baseline, proposals, verify = copy.deepcopy(inputs)
    verify["results"][0]["verdict"] = "regressed"
    verify["results"][0]["newly_broken_tests"] = ["tests/test_x.py::test_y"]
    capsule = build(evidence, baseline, proposals, verify)
    assert any("regressed" in i["text"] for i in capsule["sections"][7]["items"])
    assert any("regressed" in i["text"] for i in capsule["sections"][8]["items"])


def test_rejected_and_error(inputs):
    evidence, baseline, proposals, verify = copy.deepcopy(inputs)
    proposals["rejected"] = [{"reason": "unsafe edit"}]
    proposals["error"] = "model unavailable"
    capsule = build(evidence, baseline, proposals, verify)
    assert any("unsafe edit" in i["text"] and i["provenance"] == "executed" for i in capsule["sections"][4]["items"])
    assert any("model unavailable" in i["text"] and i["provenance"] == "unverified" for i in capsule["sections"][4]["items"])
    assert any("unsafe edit" in i["text"] for i in capsule["sections"][9]["items"])


def test_markdown_contains_labels_and_item_text(inputs):
    capsule = build(*inputs)
    md = render_markdown(capsule)
    assert "## Legend" in md
    for label in ("observed", "executed", "ai_generated", "hint", "unverified"):
        assert label in md
    for section in capsule["sections"]:
        for item in section["items"]:
            assert item["text"] in md
            assert f"- [{item['provenance']}] " in md


def test_write_round_trip(inputs, tmp_path):
    capsule = build(*inputs)
    jp, mp = write(capsule, tmp_path / "nested")
    assert json.loads(Path(jp).read_text(encoding="utf-8")) == capsule
    assert Path(mp).read_text(encoding="utf-8") == render_markdown(capsule)


def test_validate_unknown_label(inputs):
    capsule = build(*inputs)
    capsule["sections"][0]["items"][0]["provenance"] = "guess"
    assert validate_capsule(capsule)


def test_deterministic(inputs):
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert build(*inputs, now=now) == build(*inputs, now=now)


def test_at_a_glance_summary_and_missing_verify(inputs):
    capsule = build(*inputs)
    md = render_markdown(capsule)
    glance = md.split("## At a glance", 1)[1].split("## Sections", 1)[0]
    assert "- Before: 9 passed, 1 failed, 0 errors (Python 3.12.x)" in glance
    assert "- p1: improved (after: 10 passed, 0 failed, 0 errors)" in glance
    assert glance.count("Before:") == 1
    assert "After/results" not in glance

    missing_verify = render_markdown(build(inputs[0], inputs[1], inputs[2], None))
    missing_glance = missing_verify.split("## At a glance", 1)[1].split("## Sections", 1)[0]
    assert missing_glance.count("Verify: missing") == 1


def test_missing_verify_reason_is_omitted(inputs):
    capsule = build(*inputs)
    result = next(i for i in capsule["sections"][7]["items"] if i["source"].startswith("verify.json#results"))
    assert "reason=" not in result["text"]
    assert "not recorded" not in result["text"]


def test_missing_attempts_is_omitted(inputs):
    capsule = build(*inputs)
    analysis = next(i for i in capsule["sections"][4]["items"] if i["provenance"] == "ai_generated")
    assert "attempts:" not in analysis["text"]
    assert "attempts: not recorded" not in analysis["text"]


def test_install_summary_uses_success_line_or_trimmed_last_line(inputs):
    evidence, baseline, proposals, verify = copy.deepcopy(inputs)
    baseline["install"]["output_tail"] = (
        "Collecting pytest\n"
        "15.0.0-py3-none-any.whl (310 kB)\n"
        "Using cached pytest-9.1.1-py3-none-any.whl\n"
        "Successfully installed Jinja2-2.11.3 MarkupSafe-3.0.4\n"
        "extra trailing detail\n"
    )
    capsule = build(evidence, baseline, proposals, verify)
    item = next(i for i in capsule["sections"][1]["items"] if i["source"] == "baseline.json#install")
    assert item["text"] == "Install exit code 0; Successfully installed Jinja2-2.11.3 MarkupSafe-3.0.4"
    assert item["provenance"] == "executed"

    baseline["install"]["exit_code"] = 1
    baseline["install"]["output_tail"] = "Downloading archive\n" + ("x" * 350) + "\n\n"
    capsule = build(evidence, baseline, proposals, verify)
    item = next(i for i in capsule["sections"][1]["items"] if i["source"] == "baseline.json#install")
    assert item["text"] == "Install exit code 1; " + ("x" * 300)
    assert len(item["text"].split("; ", 1)[1]) == 300


CANARY = {"package": "click", "line_removed": "click>=7", "verdict": "regressed", "expected": "regressed",
          "detected": True, "reason": "passed dropped from 9 to 0",
          "before": {"exit_code": 1, "passed": 9, "failed": 1, "errors": 0},
          "after": {"exit_code": 4, "passed": 0, "failed": 0, "errors": 1}, "provenance": "executed"}


def _canary_items(capsule):
    return {s["id"]: [i for i in s["items"] if i["source"] == "canary.json"] for s in capsule["sections"]}


def test_without_canary_is_unchanged(inputs):
    now = "2026-10-15T00:00:00Z"
    assert build(*inputs, now=now) == build(*inputs, now=now, canary=None)
    assert not any(_canary_items(build(*inputs, now=now)).values())


def test_canary_detected(inputs):
    capsule = build(*inputs, canary=CANARY)
    items = _canary_items(capsule)
    assert len(items["8"]) == 1 and items["8"][0]["provenance"] == "executed"
    assert items["8"][0]["text"] == (
        "Verifier self-check (negative control): removed click from requirements.txt in a sandbox copy; "
        "verdict regressed; the verifier detected the breakage: yes "
        "(before 9 passed, 1 failed, 0 errors; after 0 passed, 0 failed, 1 errors).")
    assert not items["9"] and not items["10"]
    assert validate_capsule(capsule) == []
    glance = render_markdown(capsule).split("## At a glance", 1)[1].split("## Sections", 1)[0]
    assert "self-check" not in glance and "- p1: improved" in glance  # the canary is not a proposal result


def test_canary_missed_is_prominent(inputs):
    capsule = build(*inputs, canary=dict(CANARY, verdict="unchanged", detected=False))
    items = _canary_items(capsule)
    assert "the verifier detected the breakage: NO" in items["8"][0]["text"]
    assert items["9"][0]["text"].startswith("WARNING: the verifier missed a known-bad change (removing click")
    assert items["10"][0]["provenance"] == "unverified" and "missed a known-bad change" in items["10"][0]["text"]


@pytest.mark.parametrize("canary", [
    {"detected": None, "reason": "no suitable package", "provenance": "unverified"},
    {"detected": None, "reason": "skipped: --no-canary was given", "provenance": "unverified"},
])
def test_canary_not_judged_is_unverified(inputs, canary):
    items = _canary_items(build(*inputs, canary=canary))
    assert items["8"][0]["provenance"] == "unverified" and canary["reason"] in items["8"][0]["text"]
    assert items["10"][0]["provenance"] == "unverified" and not items["9"]


REACHED = {"package": "Jinja2", "source_files": ["linkwatch/report.py"],
           "tests_reaching": ["tests/conftest.py", "tests/test_report.py"],
           "chain": "tests/test_report.py -> linkwatch/cli.py -> linkwatch/report.py -> jinja2",
           "strength": "tests_reach_change", "provenance": "observed"}
UNREACHED = {"package": "six", "source_files": [], "tests_reaching": [], "chain": None,
             "strength": "no_test_reaches_change", "provenance": "observed"}


def _strength_items(capsule):
    return {s["id"]: [i for i in s["items"] if i["source"] == "strength.json"] for s in capsule["sections"]}


def test_without_strength_is_unchanged(inputs):
    now = "2026-10-15T00:00:00Z"
    assert build(*inputs, now=now) == build(*inputs, now=now, strength=None)
    assert not any(_strength_items(build(*inputs, now=now)).values())


def test_strength_reached(inputs):
    strength = {"schema": 1, "provenance": "observed",
                "proposals": [{"proposal_id": "p1", "packages": ["Jinja2"], "reach": [REACHED]}]}
    capsule = build(*inputs, strength=strength)
    items = _strength_items(capsule)
    assert [i["text"] for i in items["8"]] == [
        "Evidence strength for p1: 2 test file(s) reach Jinja2: "
        "tests/test_report.py -> linkwatch/cli.py -> linkwatch/report.py -> jinja2"]
    assert items["8"][0]["provenance"] == "observed" and not items["10"]
    assert validate_capsule(capsule) == []
    glance = render_markdown(capsule).split("## At a glance", 1)[1].split("## Sections", 1)[0]
    assert "Evidence strength" not in glance


def test_strength_unreached_and_unknown(inputs):
    strength = {"proposals": [{"proposal_id": "p1", "packages": ["Jinja2", "six"], "reach": [REACHED, UNREACHED]},
                              {"proposal_id": "p2", "packages": [], "reach": []}]}
    items = _strength_items(build(*inputs, strength=strength))
    texts = [i["text"] for i in items["8"]]
    assert "Evidence strength for p1: no test reaches six: the test result above cannot confirm this change" in texts
    assert any(t.startswith("Evidence strength for p2: no changed package") for t in texts)
    assert [i["provenance"] for i in items["8"]] == ["observed", "observed", "unverified"]
    assert len(items["10"]) == 1 and items["10"][0]["provenance"] == "unverified"
    assert "p1 (six)" in items["10"][0]["text"]


def test_absolute_paths_in_first_error_are_omitted(inputs):
    evidence, baseline, proposals, verify = copy.deepcopy(inputs)
    raw_error = (
        "ImportError: failed (C:\\Users\\runner\\site-packages\\markupsafe\\__init__.py) "
        "and (/opt/venv/lib/python3.12/site-packages/markupsafe.py)"
    )
    baseline["pytest"]["failures"][0]["first_error"] = raw_error
    capsule = build(evidence, baseline, proposals, verify)
    assert baseline["pytest"]["failures"][0]["first_error"] == raw_error
    item = next(i for i in capsule["sections"][0]["items"] if i["source"].startswith("baseline.json#pytest.failures"))
    assert item["text"].endswith("failed (path omitted) and (path omitted)")
    markdown = render_markdown(capsule)
    assert "(path omitted)" in markdown
    assert "C:\\Users\\runner\\site-packages" not in markdown
    assert "/opt/venv/lib/python3.12" not in markdown
