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


# ---------------------------------------------------------------- redesigned page with stage data

FAILING = "tests/test_report.py::test_report_renders_all_sites"
REPO = "D:\\Hackathons\\LinkWatch_target"
BEFORE_RUN = {"schema": 1, "python": "3.12.2", "install": {"exit_code": 0, "output_tail": "Successfully installed x"},
              "pytest": {"exit_code": 1, "passed": 9, "failed": 1, "errors": 0, "failures": [{
                  "test": FAILING, "first_error": "ImportError: cannot import name 'soft_unicode' from 'markupsafe' "
                  "(D:\\Hackathons\\DepDoctor\\out\\.sandbox\\baseline\\venv\\Lib\\site-packages\\markupsafe\\__init__.py)"}]},
              "pip_check": "No broken requirements found.", "provenance": "executed"}
AFTER_RUN = dict(BEFORE_RUN, pytest={"exit_code": 0, "passed": 10, "failed": 0, "errors": 0, "failures": []})
DIFF = ("--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1,7 +1,7 @@\n toml==0.10.2\r\n-Jinja2==2.11.3\r\n"
        "+Jinja2>=3.1,<4\r\n click>=7\r\n")
PROPOSALS = {"schema": 1, "model": "gemma4:e4b", "provenance": "ai_generated", "attempts": 1, "proposals": [{
    "id": "p1", "problem": "ImportError: cannot import name 'soft_unicode' from 'markupsafe'", "kind": "version_change",
    "evidence_refs": ["manifest:requirements.txt:4", "candidate:c4"],
    "changes": [{"file": "requirements.txt", "action": "replace_line", "old": "Jinja2==2.11.3", "new": "Jinja2>=3.1,<4"}],
    "rationale": "Jinja2 2.x imports markupsafe.soft_unicode, which MarkupSafe 2.1 removed.",
    "risk": "Template rendering could change."}], "rejected": []}
VERIFY = {"schema": 1, "results": [{
    "proposal_id": "p1", "applied_diff": DIFF, "apply_ok": True, "after": AFTER_RUN, "fixed_tests": [FAILING],
    "newly_broken_tests": [], "verdict": "improved",
    "reason": "1 test(s) fixed, none newly failing (passed 9 -> 10, failed 1 -> 0)", "provenance": "executed"}]}
CANARY = {"package": "click", "line_removed": "click>=7", "expected": "regressed",
          "before": {"exit_code": 1, "passed": 9, "failed": 1, "errors": 0}, "verdict": "regressed",
          "reason": "passed dropped from 9 to 0 (passed 9 -> 0, failed 1 -> 0)",
          "after": {"exit_code": 4, "passed": 0, "failed": 0, "errors": 0}, "newly_broken_tests": [],
          "detected": True, "provenance": "executed"}
CHAIN = "tests/test_report.py -> linkwatch/cli.py -> linkwatch/report.py -> jinja2"
STRENGTH = {"schema": 1, "provenance": "observed", "proposals": [{"proposal_id": "p1", "packages": ["Jinja2"], "reach": [{
    "package": "Jinja2", "source_files": ["linkwatch/report.py"],
    "tests_reaching": ["tests/conftest.py", "tests/test_report.py"], "chain": CHAIN,
    "strength": "tests_reach_change", "provenance": "observed"}]}]}
RUN = {"schema": 1, "repo": REPO, "origin": "fallback: direct local-model call",
       "model": {"name": "gemma4:e4b"}, "python": {"requested": "3.12", "baseline": "3.12.2"},
       "total_seconds": 158.22, "steps": [
           {"step": s, "ok": True, "seconds": t} for s, t in
           [("scan", 0.11), ("baseline", 36.19), ("propose", 56.0), ("verify", 36.43), ("canary", 29.46),
            ("strength", 0.02), ("capsule", 0.0), ("write capsule", 0.0)]]}
EVIDENCE = {"schema": 1, "repo": {"path": REPO, "commit": "f52f393", "python_requires": ">=3.10"},
            "drift": [{"package": "pydantic", "details": {"pyproject.toml": ">=2", "requirements.txt": "==1.10.13"}}],
            "undeclared": [{"import": "certifi", "file": "linkwatch/checker.py", "line": 4}],
            "unused": [{"package": "six"}, {"package": "idna"}],
            "private_api": [{"module": "requests.utils", "name": "_parse_content_type_header",
                             "file": "linkwatch/encoding.py", "line": 5}],
            "candidates": [{"id": "c1", "package": "toml", "hint": "tomllib is in the standard library from Python 3.11"}]}
DATA = {"baseline": BEFORE_RUN, "verify": VERIFY, "canary": CANARY, "strength": STRENGTH, "run": RUN,
        "proposals": PROPOSALS, "evidence": EVIDENCE}
BENEFIT_CLAIMS = ("better than", "faster than", "outperforms")


def data(**overrides):
    d = copy.deepcopy(DATA)
    d.update(overrides)
    return d


def after_card(page):
    start = page.index('<div class="kicker">After')
    return page[page.rindex('<div class="side', 0, start):page.index('<div class="row">') if '<div class="row">' in page
                else page.index("</section>", start)]


def check_common(page):
    assert page.startswith("<!doctype html>") and "<script" not in page.lower()
    assert "http://" not in page and "https://" not in page and "url(" not in page and "@import" not in page
    assert not re.search(r"<(script|link|img|iframe|object|embed)\b", page, re.IGNORECASE)
    for claim in BENEFIT_CLAIMS:
        assert claim not in page.lower()


@pytest.fixture
def full():
    return report_html.render(SAMPLE, DATA)


def test_full_page_header(full):
    check_common(full)
    for text in ("<h1>Dependency Exit Capsule</h1>", "Target: <b>LinkWatch_target @ f52f393</b>",
                 "Python: <b>3.12.2</b>", "Run time: <b>158.22 s</b>",
                 "Proposals: <b>fallback: direct local-model call</b>", "Generated: <b>2026-10-15T14:30:00+05:30</b>"):
        assert text in full


def test_hero_before_and_after(full):
    assert "Target as it is today" in full and "<b>9</b><span>passed</span>" in full
    assert FAILING in full and "cannot import name &#x27;soft_unicode&#x27; from &#x27;markupsafe&#x27; (path omitted)" in full
    card = after_card(full)
    assert 'side tone-good' in card and "With the verified fix" in card
    assert "<b>10</b><span>passed</span>" in card and "<b>0</b><span>failed</span>" in card
    assert '<span class="chip tone-good">+1 passed</span>' in card and '<span class="chip tone-good">-1 failed</span>' in card
    assert "Fixed:" in card and FAILING in card and "Newly broken: none" in card
    assert '<span class="verdict tone-good">\u2713 improved</span>' in card


def test_ai_versus_proved(full):
    assert "What the AI said vs what we proved" in full
    assert "A suggestion becomes evidence only after the tests have run." in full
    assert '<div class="col ai"><h3>AI suggestion <span class="badge ai_generated">' in full
    assert "- Jinja2==2.11.3" in full and "+ Jinja2&gt;=3.1,&lt;4" in full
    assert "Template rendering could change." in full
    assert '<div class="col proved"><h3>What DepDoctor verified <span class="badge executed">' in full
    assert "-Jinja2==2.11.3\n+Jinja2&gt;=3.1,&lt;4\n" in full and "\r" not in full
    assert "1 test(s) fixed, none newly failing (passed 9 -&gt; 10, failed 1 -&gt; 0)" in full


def test_trust_panel(full):
    assert "How much can you trust this?" in full
    assert "We removed <b>click</b> from a sandbox copy. A working verifier must flag this." in full
    assert '<span class="verdict tone-good">\u2713 Caught</span>' in full
    assert "Before: 9 passed, 1 failed, 0 errors<br>After: 0 passed, 0 failed, 0 errors" in full
    assert "2 test file(s) reach <b>Jinja2</b>" in full
    assert ("<span>tests/test_report.py</span><i>&rarr;</i><span>linkwatch/cli.py</span><i>&rarr;</i>"
            "<span>linkwatch/report.py</span><i>&rarr;</i><span>jinja2</span>") in full
    assert "Reach means a test imports a path to the changed code; it does not prove the changed lines ran." in full


def test_timeline(full):
    timeline = full[full.index('id="timeline"'):full.index('id="findings"')]
    labels = re.findall(r"<b>([^<]+)</b> ([\d.]+ s)", timeline)
    assert labels == [("scan", "0.11 s"), ("baseline tests", "36.19 s"), ("propose", "56.0 s"),
                      ("verify", "36.43 s"), ("self-check", "29.46 s"), ("strength", "0.02 s"), ("capsule", "0.0 s")]
    assert '<span class="dot ai_generated"' in timeline and "(AI (local model))" in timeline
    assert '<span class="dot executed"' in timeline and "validate" not in timeline  # missing step omitted


def test_findings_and_limits(full):
    findings = full[full.index('id="findings"'):full.index('id="limits"')]
    assert "certifi is imported but not declared" in findings and "linkwatch/checker.py:4" in findings
    assert "requests.utils._parse_content_type_header" in findings and "six is declared but never imported" in findings
    assert "pydantic: pyproject.toml &#x27;&gt;=2&#x27;; requirements.txt &#x27;==1.10.13&#x27;" in findings
    assert findings.count('class="badge observed"') == 5
    assert "hint(s) from the built-in table (hint, not verified)" in findings
    assert "<details open>" in findings
    limits = full[full.index('id="limits"'):full.index("<footer>")]
    assert "Template output was not compared visually" in limits and 'class="badge unverified"' in limits


def test_full_report_keeps_every_section(full):
    for sid in map(str, range(1, 11)):
        assert f"<h2>{sid}. {TITLES[sid]}</h2>" in full
    assert '<details class="card section" id="s8" open>' in full
    assert '<details class="card section" id="s1">' in full  # collapsed


def test_findings_collapse_after_six_rows():
    ev = dict(EVIDENCE, unused=[{"package": f"pkg{i}"} for i in range(8)])
    page = report_html.render(SAMPLE, data(evidence=ev))
    assert "<summary>Show 5 more</summary>" in page  # 1 drift + 1 undeclared + 8 unused + 1 private = 11


def test_data_none_falls_back():
    page = report_html.render(SAMPLE, None)
    check_common(page)
    assert "Stage files (baseline, verify, canary, strength, run) were not provided" in page
    assert "Target as it is today" not in page and "<h2>At a glance</h2>" in page
    assert report_html.render(SAMPLE, {"verify": None, "run": "junk"}) == page


def test_verify_missing():
    page = report_html.render(SAMPLE, data(verify=None))
    check_common(page)
    card = after_card(page)
    assert "side tone-warn" in card and "No verified fix" in card and "verify.json was not provided" in card
    assert "With the verified fix" not in page
    assert "Not verified: no test run exists for this proposal." in page


@pytest.mark.parametrize("verdict,tone", [("regressed", "bad"), ("unchanged", "warn"), ("could_not_run", "warn")])
def test_after_card_never_green_unless_improved(verdict, tone):
    v = copy.deepcopy(VERIFY)
    v["results"][0].update(verdict=verdict, fixed_tests=[], reason=f"because {verdict}")
    if verdict == "regressed":
        v["results"][0].update(after=dict(AFTER_RUN, pytest={"exit_code": 1, "passed": 7, "failed": 3, "errors": 0,
                                                              "failures": []}), newly_broken_tests=["tests/test_x.py::t"])
    page = report_html.render(SAMPLE, data(verify=v))
    check_common(page)
    card = after_card(page)
    assert f"side tone-{tone}" in card and "side tone-good" not in card
    assert "No verified fix" in card and "With the verified fix" not in page
    assert f"{report_html.VERDICTS[verdict][1]} {verdict}</span>" in card
    if verdict == "regressed":
        assert '<span class="chip tone-bad">-2 passed</span>' in card and "tests/test_x.py::t" in card


def test_proposals_empty():
    page = report_html.render(SAMPLE, data(proposals={"proposals": [], "rejected": [{"reason": "bad ref"}]},
                                           verify={"schema": 1, "results": []}, strength=None))
    check_common(page)
    assert "No proposal was produced, so nothing was applied or tested." in page
    assert "no proposal was verified" in after_card(page)
    assert "1 proposal(s) rejected by the validator" in page and "bad ref" in page


def test_several_proposals_get_a_row():
    v = copy.deepcopy(VERIFY)
    v["results"].append(dict(v["results"][0], proposal_id="p2", verdict="regressed", after=BEFORE_RUN))
    page = report_html.render(SAMPLE, data(verify=v))
    row = page[page.index('<div class="row">'):]
    assert "<b>p1</b>" in row and "<b>p2</b>" in row and "\u2717 regressed" in row
    assert "After &middot; p1" in page  # the best result is the hero


def test_canary_missed_is_red():
    page = report_html.render(SAMPLE, data(canary=dict(CANARY, verdict="improved", detected=False)))
    check_common(page)
    assert '<span class="verdict tone-bad">\u2717 MISSED</span>' in page
    assert "The verifier missed a known-bad change" in page and "\u2713 Caught" not in page


def test_canary_not_run():
    page = report_html.render(SAMPLE, data(canary={"detected": None, "reason": "skipped: --no-canary was given"}))
    assert '<span class="verdict tone-warn">! Not run</span>' in page and "skipped: --no-canary was given" in page
    assert "canary.json was not provided" in report_html.render(SAMPLE, data(canary=None))


def test_strength_without_reaching_test():
    s = copy.deepcopy(STRENGTH)
    s["proposals"][0]["reach"][0].update(tests_reaching=[], chain=None, strength="no_test_reaches_change")
    page = report_html.render(SAMPLE, data(strength=s))
    check_common(page)
    assert "No test reaches this change: a green result is not evidence" in page
    assert "test file(s) reach" not in page


def test_script_in_rationale_is_escaped():
    p = copy.deepcopy(PROPOSALS)
    p["proposals"][0]["rationale"] = "<script>alert(1)</script>"
    p["proposals"][0]["changes"][0]["new"] = "<img src=x onerror=alert(1)>"
    page = report_html.render(SAMPLE, data(proposals=p))
    check_common(page)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;img src=x" in page


def test_absolute_paths_are_shortened():
    v = copy.deepcopy(VERIFY)
    v["results"][0]["reason"] = f"failed in {REPO}\\linkwatch\\report.py and /opt/venv/lib/site.py"
    page = report_html.render(SAMPLE, data(verify=v))
    assert "failed in linkwatch/report.py and (path omitted)" in page
    assert "D:\\Hackathons" not in page and "/opt/venv" not in page


def test_clean():
    assert report_html.clean("x (C:\\a\\b.py) y") == "x (path omitted) y"
    assert report_html.clean("see /usr/lib/x.py", None) == "see (path omitted)"
    assert report_html.clean("D:\\r\\pkg\\m.py", "D:\\r") == "pkg/m.py"
    for keep in ("a/requirements.txt", "https://www.python.org", "</script>", "Model: gemma4:e4b"):
        assert report_html.clean(keep) == keep


def test_load_stage_files_and_report_cli(tmp_path):
    from tool.cli import main
    names = {"baseline.json": BEFORE_RUN, "verify.json": VERIFY, "canary.json": CANARY, "strength.json": STRENGTH,
             "run.json": RUN, "evidence.json": EVIDENCE, "proposals.fallback.json": PROPOSALS,
             "proposals.json": {"proposals": [dict(PROPOSALS["proposals"][0], problem="agent version")]}}
    for name, content in names.items():
        (tmp_path / name).write_text(json.dumps(content), encoding="utf-8")
    (tmp_path / "capsule.json").write_text(json.dumps(SAMPLE), encoding="utf-8")
    loaded = report_html.load_stage_files(tmp_path)
    assert loaded["proposals"] == PROPOSALS  # run.json says fallback, so the fallback file is the one used
    assert main(["report", str(tmp_path / "capsule.json"), "--out", str(tmp_path / "c.html")]) == 0
    page = (tmp_path / "c.html").read_text(encoding="utf-8")
    assert page == report_html.render(SAMPLE, loaded) and "Target as it is today" in page
    assert report_html.load_stage_files(tmp_path / "nothing-here") is None
