import copy
import json
import re

import pytest

from tool import report_html

TITLES = {
    "1": "Maintenance problem identified", "2": "Repository and dependency details",
    "3": "Risk indicators", "4": "Usage locations", "5": "AI analysis", "6": "Proposed changes",
    "7": "Suggested tests", "8": "Executed results", "9": "Gaps", "10": "Unresolved and limitations",
}
ITEMS = {
    "1": [("Jinja2 2.11.3 fails to import with MarkupSafe 3.x", "observed", "evidence.json#manifests[3]")],
    "2": [("pyproject.toml says pydantic >=2; requirements.txt says ==1.10.13", "observed", "evidence.json#drift[0]")],
    "3": [("1 of 10 tests fails before any change", "executed", "baseline.json#pytest")],
    "4": [("jinja2 imported in linkwatch/report.py:3", "observed", "evidence.json#usage[14]")],
    "5": [("Jinja2 3.x no longer imports soft_unicode", "ai_generated", "proposals.json#proposals[0]")],
    "6": [("--- a/requirements.txt\n+++ b/requirements.txt\n-Jinja2==2.11.3\n+Jinja2>=3.1,<4\n",
           "executed", "verify.json#results[0].applied_diff")],
    "7": [("Render the report with an empty site list", "ai_generated", "proposals.json#proposals[0]")],
    "8": [("Before: 9 passed, 1 failed", "executed", "baseline.json#pytest"),
          ("After p1: 10 passed, 0 failed (improved)", "executed", "verify.json#results[0]"),
          ("Command: python -m pytest -q", "executed", "verify.json#results[0].after"),
          ("pip check: No broken requirements found.", "executed", "verify.json#results[0].after")],
    "9": [("toml -> tomllib is a candidate but was not proposed", "hint", "evidence.json#candidates[0]")],
    "10": [("Template output was not compared visually", "unverified", "")],
}
SAMPLE = {
    "schema": 1,
    "generated_at": "2026-10-15T14:30:00+05:30",
    "model": {"name": "gemma4:e4b", "source": "Ollama local", "agent": "Hermes Agent", "license": "Apache-2.0"},
    # deliberately out of order: the page must show them 1..10
    "sections": [{"id": sid, "title": TITLES[sid],
                  "items": [{"text": t, "provenance": p, "source": s} for t, p, s in ITEMS[sid]]}
                 for sid in ["10", "2", "1", "3", "4", "5", "6", "7", "8", "9"]],
}


@pytest.fixture
def page():
    return report_html.render(SAMPLE)


def test_renders_complete_document(page):
    assert page.startswith("<!doctype html>") and page.rstrip().endswith("</html>")
    assert "<title>Dependency Exit Capsule</title>" in page and "<h1>Dependency Exit Capsule</h1>" in page
    assert "2026-10-15T14:30:00+05:30" in page
    assert "gemma4:e4b · Ollama local · Hermes Agent · Apache-2.0" in page
    assert "prefers-color-scheme: dark" in page and 'name="viewport"' in page


def test_every_section_title_appears_in_order(page):
    positions = [page.index(f"<h2>{sid}. {TITLES[sid]}</h2>") for sid in map(str, range(1, 11))]
    assert positions == sorted(positions)


def test_every_used_badge_label_appears(page):
    for label in ("observed", "executed", "ai_generated", "hint", "unverified"):
        assert f'class="badge {label}"' in page
        # the label is shown as text in each item row, not only in the legend
        assert page.count(f'class="badge {label}"') >= 2
        assert f"{report_html.PROVENANCE[label][0]} {label}</span>" in page


def test_unknown_provenance_is_marked_not_hidden():
    capsule = copy.deepcopy(SAMPLE)
    capsule["sections"][0]["items"].append({"text": "x", "provenance": "guess<b>", "source": ""})
    page = report_html.render(capsule)
    assert '<span class="badge unknown">? guess&lt;b&gt;</span>' in page


def test_script_in_item_text_is_escaped():
    capsule = copy.deepcopy(SAMPLE)
    evil = "<script>alert(1)</script>"
    capsule["sections"][0]["items"].append({"text": evil, "provenance": "observed", "source": evil})
    capsule["sections"][1]["title"] = evil
    capsule["generated_at"] = evil
    capsule["model"]["name"] = evil
    capsule["sections"][2]["items"].append({"text": "line1\n" + evil, "provenance": "hint", "source": "s"})
    page = report_html.render(capsule)
    assert "<script" not in page.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_no_external_resources(page):
    assert "http://" not in page and "https://" not in page
    assert not re.search(r"<(script|link|img|iframe|object|embed)\b", page, re.IGNORECASE)
    assert "@import" not in page and "url(" not in page


def test_urls_in_text_stay_text():
    capsule = copy.deepcopy(SAMPLE)
    capsule["sections"][0]["items"][0]["text"] = "site https://www.python.org was checked"
    page = report_html.render(capsule)
    assert "<a href=\"https" not in page and "src=" not in page
    assert "site https://www.python.org was checked" in page


def test_multiline_and_diff_items_use_pre(page):
    assert "<pre>--- a/requirements.txt\n+++ b/requirements.txt\n-Jinja2==2.11.3\n+Jinja2&gt;=3.1,&lt;4\n</pre>" in page
    assert "<pre>---" in page
    assert "<p>Before: 9 passed, 1 failed</p>" in page  # single line stays a paragraph


def test_at_a_glance_shows_first_items_of_section_8(page):
    glance = page[page.index("<h2>At a glance</h2>"):page.index("<nav")]
    assert "Before: 9 passed, 1 failed" in glance and "After p1: 10 passed, 0 failed (improved)" in glance
    assert "Command: python -m pytest -q" in glance
    assert "pip check" not in glance  # only the first three
    assert 'href="#s8"' in glance


def test_at_a_glance_without_section_8():
    capsule = copy.deepcopy(SAMPLE)
    capsule["sections"] = [s for s in capsule["sections"] if s["id"] != "8"]
    page = report_html.render(capsule)
    assert "No executed results in this capsule." in page


def test_sources_shown_and_empty_source_omitted(page):
    assert '<div class="source">source: baseline.json#pytest</div>' in page
    assert "source: </div>" not in page


def test_tolerates_sparse_capsule():
    page = report_html.render({"sections": [{"id": "1", "title": "Only", "items": []}, "junk"]})
    assert "Model: not recorded" in page and "No items in this section." in page


def test_write_and_main(tmp_path, capsys):
    src, out = tmp_path / "capsule.json", tmp_path / "capsule.html"
    src.write_text(json.dumps(SAMPLE), encoding="utf-8")
    assert report_html.main([str(src), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == report_html.render(SAMPLE)
    assert "wrote" in capsys.readouterr().out
    assert report_html.main([str(tmp_path / "missing.json"), "--out", str(out)]) == 2
