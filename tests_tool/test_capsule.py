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
