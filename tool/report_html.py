"""Render a capsule as one self-contained HTML page (inline CSS, no scripts, no external files).

    py -3.12 -m tool.report_html capsule.json --out capsule.html

render(capsule, data) uses the parsed stage files (baseline, verify, canary, strength, run, proposals, evidence)
for every number on the page: a Before -> After panel, what the AI said next to what the tests proved, a trust
panel (verifier self-check and test reach), the run timeline, other findings and limits. Without stage data the
page falls back to the capsule's own sections and says so. Free text is only displayed, never parsed; every piece
of text is escaped, and absolute paths are shortened.
"""

import argparse
import json
import re
import sys
from html import escape
from pathlib import Path, PurePosixPath, PureWindowsPath

# label -> (mark, explanation). The mark and the label text tell them apart without colour.
PROVENANCE = {
    "observed": ("\u2713", "read from the repository files by deterministic code"),
    "executed": ("\u25b6", "produced by running a command (install, pytest) and recording its output"),
    "ai_generated": ("\u2726", "written by the local model; trusted only where an executed result backs it"),
    "hint": ("\u2139", "from DepDoctor's built-in table of known migrations; a suggestion, not a finding"),
    "unverified": ("!", "not checked by any run; treat as an open question"),
}
# verdict -> (css tone, mark). Only "improved" is ever green.
VERDICTS = {"improved": ("good", "\u2713"), "unchanged": ("neutral", "="), "regressed": ("bad", "\u2717"),
            "could_not_run": ("warn", "!")}
STAGE_FILES = {"baseline": "baseline.json", "verify": "verify.json", "canary": "canary.json",
               "strength": "strength.json", "run": "run.json", "evidence": "evidence.json"}
# run.json step -> (label shown, kind). kind picks the dot colour and the text next to it.
TIMELINE = [("scan", "scan", "static"), ("baseline", "baseline tests", "ran"), ("propose", "propose", "ai"),
            ("validate", "validate", "static"), ("verify", "verify", "ran"), ("canary", "self-check", "ran"),
            ("strength", "strength", "static"), ("capsule", "capsule", "report")]
STEP_KINDS = {"static": ("observed", "static analysis"), "ran": ("executed", "ran code"),
              "ai": ("ai_generated", "AI (local model)"), "report": ("hint", "report")}
GLANCE_SECTION = "8"
GLANCE_ITEMS = 3
FINDINGS_OPEN = 6

PAREN_PATH = re.compile(r"\((?:[A-Za-z]:[\\/]|/)[^()]*\)")
WIN_PATH = re.compile(r"\b[A-Za-z]:[\\/][^\s'\"()<>,;]*")
POSIX_PATH = re.compile(r"(?<![\w:/.<>-])/(?:[^\s/'\"()<>,;]+/)+[^\s'\"()<>,;]*")

CSS = """
:root {
  --bg: #f7f7f5; --card: #ffffff; --text: #1d1d1f; --muted: #5f6368; --border: #e2e2de;
  --pre-bg: #f1f1ee; --link: #1747a0;
  --observed-bg: #e3f4e8; --observed-fg: #17602f;
  --executed-bg: #e2edfb; --executed-fg: #1747a0;
  --ai_generated-bg: #efe6fb; --ai_generated-fg: #5b2a9e;
  --hint-bg: #ececec; --hint-fg: #444444;
  --unverified-bg: #fdf0d5; --unverified-fg: #7a4b00;
  --bad-bg: #fde4e1; --bad-fg: #a3231a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141416; --card: #1d1d20; --text: #ececee; --muted: #a0a0a8; --border: #2e2e33;
    --pre-bg: #111113; --link: #9cc2ff;
    --observed-bg: #163522; --observed-fg: #8fe0a8;
    --executed-bg: #152a4a; --executed-fg: #9cc2ff;
    --ai_generated-bg: #2c1d45; --ai_generated-fg: #d2b6ff;
    --hint-bg: #2c2c30; --hint-fg: #c8c8cc;
    --unverified-bg: #3d2c0c; --unverified-fg: #ffd27a;
    --bad-bg: #45191a; --bad-fg: #ffaba3;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: var(--link); }
main { max-width: 1000px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.75rem; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 0 0 12px; }
h3 { font-size: 1rem; margin: 0 0 8px; }
.meta { color: var(--muted); font-size: .9rem; margin: 2px 0; }
.mono, pre, code, .source, .badge, .chain span {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px; margin: 16px 0;
}
.bar { display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: baseline; padding: 12px 18px; }
.bar h1 { font-size: 1.3rem; margin: 0 12px 0 0; }
.bar .kv { font-size: .85rem; color: var(--muted); }
.bar .kv b { color: var(--text); font-weight: 600; }
.legend { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.legend li { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.legend .why, .note { color: var(--muted); font-size: .9rem; }
.badge {
  display: inline-block; white-space: nowrap; font-size: .78rem; font-weight: 600;
  padding: 1px 8px; border-radius: 999px; border: 1px solid currentColor;
}
""" + "".join(f".badge.{p} {{ background: var(--{p}-bg); color: var(--{p}-fg); }}\n" for p in PROVENANCE) + """
.badge.unknown { background: var(--hint-bg); color: var(--hint-fg); border-style: dashed; }
.tone-good { background: var(--observed-bg); color: var(--observed-fg); }
.tone-neutral { background: var(--hint-bg); color: var(--hint-fg); }
.tone-warn { background: var(--unverified-bg); color: var(--unverified-fg); }
.tone-bad { background: var(--bad-bg); color: var(--bad-fg); }
.verdict { display: inline-block; font-weight: 700; font-size: .85rem; padding: 2px 10px; border-radius: 999px;
           border: 1px solid currentColor; white-space: nowrap; }
nav { font-size: .9rem; display: flex; flex-wrap: wrap; gap: 4px 12px; }
nav a { color: var(--muted); white-space: nowrap; }
.items { list-style: none; margin: 0; padding: 0; }
.item { display: grid; grid-template-columns: 9.5rem 1fr; gap: 4px 12px; padding: 10px 0;
        border-top: 1px solid var(--border); }
.item:first-child { border-top: 0; padding-top: 0; }
.item .body { min-width: 0; }
.item p { margin: 0; overflow-wrap: anywhere; }
.source { color: var(--muted); font-size: .8rem; margin-top: 2px; overflow-wrap: anywhere; }
pre {
  margin: 0; padding: 10px 12px; background: var(--pre-bg); border-radius: 6px; overflow-x: auto;
  font-size: .82rem; line-height: 1.45; white-space: pre;
}
.empty { color: var(--muted); font-style: italic; margin: 0; }
.glance { border-width: 2px; }
.hero { display: grid; grid-template-columns: 1fr auto 1fr; gap: 14px; align-items: stretch; }
.hero .arrow { align-self: center; font-size: 2.2rem; color: var(--muted); }
.side { border-radius: 12px; padding: 16px 18px; border: 2px solid var(--border); background: var(--card); }
.side[class*="tone-"] { background: var(--card); color: var(--text); }
.side.tone-good { border-color: var(--observed-fg); }
.side.tone-warn { border-color: var(--unverified-fg); }
.side.tone-bad { border-color: var(--bad-fg); }
.side.tone-good > h3, .side.tone-good .kicker, .side.tone-good .big b { color: var(--observed-fg); }
.side.tone-warn > h3, .side.tone-warn .kicker, .side.tone-warn .big b { color: var(--unverified-fg); }
.side.tone-bad > h3, .side.tone-bad .kicker, .side.tone-bad .big b { color: var(--bad-fg); }
.side h3 { margin: 0 0 2px; font-size: 1rem; }
.kicker { text-transform: uppercase; letter-spacing: .06em; font-size: .75rem; font-weight: 700; }
.big { display: flex; gap: 18px; flex-wrap: wrap; margin: 8px 0; }
.big div { display: flex; flex-direction: column; }
.big b { font-size: 2.3rem; line-height: 1.1; }
.big span { font-size: .8rem; color: inherit; opacity: .85; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
.chip { font-size: .8rem; font-weight: 600; padding: 1px 8px; border-radius: 6px; border: 1px solid currentColor; }
.tests { margin: 6px 0 0; padding-left: 18px; font-size: .88rem; }
.tests li { overflow-wrap: anywhere; }
.tests .err { display: block; color: var(--muted); font-size: .8rem; }
.row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-top: 12px; }
.mini { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: .88rem; }
.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 10px; }
.col { border-radius: 10px; padding: 12px 14px; border: 1px solid; min-width: 0; }
.col.ai { background: var(--ai_generated-bg); border-color: var(--ai_generated-fg); }
.col.proved { background: var(--executed-bg); border-color: var(--executed-fg); }
.col h3 { font-size: .95rem; }
.col p { margin: 6px 0; overflow-wrap: anywhere; }
.col pre { background: var(--card); }
.change { font-size: .85rem; overflow-wrap: anywhere; }
.del { color: var(--bad-fg); } .add { color: var(--observed-fg); }
.caption { color: var(--muted); font-size: .9rem; margin: 4px 0 0; font-style: italic; }
.trust { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.chain { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin: 6px 0; }
.chain span { font-size: .78rem; padding: 1px 6px; border-radius: 4px; border: 1px solid var(--border);
              background: var(--pre-bg); overflow-wrap: anywhere; }
.chain i { font-style: normal; color: var(--muted); }
.steps { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
.steps li { border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-size: .88rem; }
.steps .dot { display: inline-block; width: .7em; height: .7em; border-radius: 50%; margin-right: 6px;
              border: 1px solid currentColor; vertical-align: baseline; }
""" + "".join(f".dot.{p} {{ background: var(--{p}-fg); color: var(--{p}-fg); }}\n" for p in PROVENANCE) + """
.steps .kind { color: var(--muted); font-size: .78rem; }
.steps .failed { color: var(--bad-fg); font-weight: 700; }
.findings { display: grid; gap: 0; }
.finding { display: grid; grid-template-columns: 8.5rem 1fr auto; gap: 4px 12px; padding: 7px 0;
           border-top: 1px solid var(--border); font-size: .9rem; align-items: baseline; }
.finding .where { color: var(--muted); font-size: .8rem; }
.finding p { margin: 0; overflow-wrap: anywhere; }
details > summary { cursor: pointer; }
details.section > summary { list-style: none; }
details.section > summary h2 { display: inline; }
details.section > summary::before { content: "\\25b8  "; color: var(--muted); }
details.section[open] > summary::before { content: "\\25be  "; }
footer .card { font-size: .95rem; }
@media (max-width: 700px) {
  .hero, .compare, .trust { grid-template-columns: 1fr; }
  .hero .arrow { transform: rotate(90deg); justify-self: center; }
  .item, .finding { grid-template-columns: 1fr; }
  h1 { font-size: 1.45rem; }
}
@media print {
  body { background: #fff; font-size: 12px; }
  main { max-width: none; padding: 0; }
  nav, .no-print { display: none; }
  .card, .side, .col, .mini { break-inside: avoid; }
  .card { margin: 8px 0; padding: 10px 12px; }
  .big b { font-size: 1.6rem; }
}
"""


# ---------------------------------------------------------------- text helpers

def clean(text, root=None):
    """Shorten absolute paths: inside the repo to the relative part, anything else to '(path omitted)'."""
    text = str(text)
    roots = []
    if root:
        r = str(root).rstrip("\\/")
        roots = [r, r.replace("\\", "/"), r.replace("/", "\\")]

    def shorten(match):
        path = match.group(0)
        inner = path[1:-1] if path.startswith("(") else path
        for r in roots:
            if inner.lower().startswith(r.lower() + "\\") or inner.lower().startswith(r.lower() + "/"):
                rel = inner[len(r) + 1:].replace("\\", "/")
                return f"({rel})" if path.startswith("(") else rel
        return "(path omitted)"

    text = PAREN_PATH.sub(shorten, text)
    text = WIN_PATH.sub(shorten, text)
    return POSIX_PATH.sub(shorten, text)


class Page:
    """Holds the repo root so every displayed string is cleaned and escaped the same way."""

    def __init__(self, root=None):
        self.root = root

    def t(self, value, limit=None):
        text = clean("" if value is None else value, self.root)
        if limit and len(text) > limit:
            text = text[:limit - 1].rstrip() + "\u2026"
        return escape(text)


def badge(provenance):
    label = str(provenance or "missing")
    if label in PROVENANCE:
        mark = PROVENANCE[label][0]
        return f'<span class="badge {label}">{mark} {escape(label)}</span>'
    return f'<span class="badge unknown">? {escape(label)}</span>'


def verdict_badge(verdict):
    tone, mark = VERDICTS.get(verdict, ("warn", "?"))
    return f'<span class="verdict tone-{tone}">{mark} {escape(str(verdict or "no verdict"))}</span>'


def _dict(value):
    return value if isinstance(value, dict) else None


def _counts(run):
    """(passed, failed, errors) from a baseline-shaped dict or a pytest dict; None if tests did not run."""
    pytest = _dict(run) or {}
    pytest = _dict(pytest.get("pytest")) or pytest
    if pytest.get("passed") is None:
        return None
    return tuple(int(pytest.get(k) or 0) for k in ("passed", "failed", "errors"))


def _counts_text(counts):
    return "tests did not run" if counts is None else "{} passed, {} failed, {} errors".format(*counts)


def _big(counts):
    if counts is None:
        return '<p class="empty">Tests did not run.</p>'
    return '<div class="big">' + "".join(
        f"<div><b>{n}</b><span>{label}</span></div>" for n, label in zip(counts, ("passed", "failed", "errors"))
    ) + "</div>"


# ---------------------------------------------------------------- capsule items (fallback and full report)

def render_item(item, page=None):
    page = page or Page()
    item = item if isinstance(item, dict) else {"text": item}
    text = str(item.get("text", ""))
    if "\n" in text or text.startswith("---"):
        body = f"<pre>{page.t(text)}</pre>"
    else:
        body = f"<p>{page.t(text)}</p>"
    if isinstance(item.get("diff"), str) and item["diff"]:
        body += f"<pre>{page.t(item['diff'].replace(chr(13), ''))}</pre>"
    source = item.get("source")
    if source:
        body += f'<div class="source">source: {page.t(source)}</div>'
    return f'<li class="item"><div>{badge(item.get("provenance"))}</div><div class="body">{body}</div></li>'


def render_items(items, empty="No items in this section.", page=None):
    if not items:
        return f'<p class="empty">{escape(empty)}</p>'
    return '<ul class="items">' + "".join(render_item(i, page) for i in items) + "</ul>"


def _sort_key(section):
    sid = str(section.get("id", ""))
    return (0, int(sid), "") if sid.isdigit() else (1, 0, sid)


def _sections(capsule):
    return sorted((s for s in capsule.get("sections") or [] if isinstance(s, dict)), key=_sort_key)


def _full_report(sections, page, open_ids):
    out = []
    for s in sections:
        sid = escape(str(s.get("id")))
        is_open = " open" if open_ids is None or str(s.get("id")) in open_ids else ""
        out.append(f'<details class="card section" id="s{sid}"{is_open}><summary>'
                   f'<h2>{sid}. {escape(str(s.get("title", "")))}</h2></summary>'
                   f'{render_items(s.get("items") or [], page=page)}</details>')
    return "".join(out)


def _legend():
    rows = "".join(f'<li>{badge(p)}<span class="why">{escape(why)}</span></li>' for p, (_, why) in PROVENANCE.items())
    return f'<section class="card" aria-label="Provenance legend"><h2>How each line is known</h2>' \
           f'<ul class="legend">{rows}</ul></section>'


def _model_line(capsule):
    model = capsule.get("model") or {}
    return " \u00b7 ".join(escape(str(model[k])) for k in ("name", "source", "agent", "license") if model.get(k))


# ---------------------------------------------------------------- panels (stage data)

def _header(capsule, data, page):
    run, evidence, baseline = data["run"] or {}, data["evidence"] or {}, data["baseline"] or {}
    repo_path = run.get("repo") or ((evidence.get("repo") or {}).get("path"))
    name = None
    if repo_path:
        name = (PureWindowsPath if "\\" in str(repo_path) else PurePosixPath)(str(repo_path)).name
    commit = (evidence.get("repo") or {}).get("commit")
    python = baseline.get("python") or (run.get("python") or {}).get("baseline")
    model = _model_line(capsule)
    pairs = [("Target", f"{name or 'not recorded'}" + (f" @ {commit}" if commit else "")),
             ("Python", python or "not recorded"),
             ("Generated", capsule.get("generated_at") or "unknown"),
             ("Run time", f"{run['total_seconds']} s" if run.get("total_seconds") is not None else "not recorded"),
             ("Proposals", run.get("origin") or "not recorded")]
    kv = "".join(f'<span class="kv">{escape(k)}: <b>{page.t(v)}</b></span>' for k, v in pairs)
    return (f'<header class="card bar"><h1>Dependency Exit Capsule</h1>{kv}'
            f'<span class="kv">Model: <b>{model or "not recorded"}</b></span></header>')


def _results(data):
    return [r for r in ((data["verify"] or {}).get("results") or []) if isinstance(r, dict)]


def _best(results):
    rank = {"improved": 0, "unchanged": 1, "regressed": 2, "could_not_run": 3}
    return min(results, key=lambda r: (rank.get(r.get("verdict"), 4), -((_counts(r.get("after")) or (0,))[0])),
               default=None)


def _delta_chips(before, after):
    if before is None or after is None:
        return ""
    chips = []
    for (label, b, a, up_is_good) in (("passed", before[0], after[0], True), ("failed", before[1], after[1], False),
                                      ("errors", before[2], after[2], False)):
        d = a - b
        if d:
            good = (d > 0) == up_is_good
            chips.append(f'<span class="chip tone-{"good" if good else "bad"}">{d:+d} {label}</span>')
    if not chips:
        chips.append('<span class="chip tone-neutral">no change in counts</span>')
    return f'<div class="chips">{"".join(chips)}</div>'


def _test_list(names, page, label):
    if not names:
        return f'<p class="meta">{escape(label)}: none</p>'
    return (f'<p class="meta">{escape(label)}:</p><ul class="tests">'
            + "".join(f"<li>{page.t(n)}</li>" for n in names) + "</ul>")


def _hero(data, page):
    baseline = data["baseline"]
    before = _counts(baseline)
    if baseline is None:
        left = ('<div class="side tone-warn"><div class="kicker">Before</div><h3>Target as it is today</h3>'
                f'<p>{badge("unverified")} baseline.json was not provided.</p></div>')
    else:
        failures = ((baseline.get("pytest") or {}).get("failures")) or []
        fails = "".join(f'<li>{page.t(f.get("test"))}<span class="err">{page.t(f.get("first_error"), 160)}</span></li>'
                        for f in failures if isinstance(f, dict))
        left = ('<div class="side"><div class="kicker">Before</div><h3>Target as it is today</h3>'
                f'{_big(before)}{badge("executed")} <span class="meta">baseline test run</span>'
                + (f'<p class="meta">Failing:</p><ul class="tests">{fails}</ul>' if fails else
                   '<p class="meta">Failing: none</p>') + "</div>")

    results = _results(data)
    best = _best(results)
    if best is None:
        why = "verify.json was not provided" if data["verify"] is None else "no proposal was verified"
        right = ('<div class="side tone-warn"><div class="kicker">After</div><h3>No verified fix</h3>'
                 f'<p>{badge("unverified")} {escape(why)}.</p></div>')
    else:
        verdict = best.get("verdict")
        after = _counts(best.get("after"))
        tone = {"improved": "good", "regressed": "bad"}.get(verdict, "warn")
        title = "With the verified fix" if verdict == "improved" else "No verified fix"
        right = (f'<div class="side tone-{tone}"><div class="kicker">After &middot; {page.t(best.get("proposal_id"))}'
                 f'</div><h3>{title}</h3>{_big(after)}{verdict_badge(verdict)} {badge("executed")}'
                 f'{_delta_chips(before, after)}'
                 f'{_test_list(best.get("fixed_tests"), page, "Fixed")}'
                 f'{_test_list(best.get("newly_broken_tests"), page, "Newly broken")}</div>')
    row = ""
    if len(results) > 1:
        row = '<div class="row">' + "".join(
            f'<div class="mini"><b>{page.t(r.get("proposal_id"))}</b> {verdict_badge(r.get("verdict"))}'
            f'<div class="meta">{_counts_text(_counts(r.get("after")))}</div></div>' for r in results) + "</div>"
    return (f'<section class="card" id="hero" aria-label="Before and after"><h2>Before &rarr; After</h2>'
            f'<div class="hero">{left}<div class="arrow" aria-hidden="true">&rarr;</div>{right}</div>{row}</section>')


def _change_lines(changes, page):
    out = []
    for c in changes or []:
        if not isinstance(c, dict):
            continue
        action, file = c.get("action"), page.t(c.get("file"))
        lines = []
        if c.get("old") is not None and action != "create_file":
            lines.append(f'<span class="del">- {page.t(c.get("old"), 200)}</span>')
        if c.get("new") is not None and action != "delete_line":
            lines.append(f'<span class="add">+ {page.t(c.get("new"), 200)}</span>')
        out.append(f'<div class="change mono">{file} ({page.t(action)})<br>{"<br>".join(lines)}</div>')
    return "".join(out) or '<p class="empty">No change recorded.</p>'


def _compare(data, page):
    proposals = [p for p in ((data["proposals"] or {}).get("proposals") or []) if isinstance(p, dict)]
    results = {r.get("proposal_id"): r for r in _results(data)}
    before = _counts(data["baseline"])
    ids = [p.get("id") for p in proposals] + [i for i in results if i not in {p.get("id") for p in proposals}]
    if not ids:
        body = f'<p>{badge("unverified")} No proposal was produced, so nothing was applied or tested.</p>'
    else:
        body = ""
        by_id = {p.get("id"): p for p in proposals}
        for pid in ids:
            p, r = by_id.get(pid), results.get(pid)
            if p:
                ai = (f'<p><b>Problem:</b> {page.t(p.get("problem"))}</p>{_change_lines(p.get("changes"), page)}'
                      f'<p><b>Rationale:</b> {page.t(p.get("rationale"))}</p>'
                      f'<p><b>Stated risk:</b> {page.t(p.get("risk"))}</p>')
            else:
                ai = f'<p>{badge("unverified")} The proposal text is not available.</p>'
            if r:
                diff = (r.get("applied_diff") or "").replace("\r", "")
                proved = ((f"<pre>{page.t(diff)}</pre>" if diff else '<p class="empty">No diff was applied.</p>')
                          + f'<p><b>Before:</b> {_counts_text(before)}<br><b>After:</b> '
                            f'{_counts_text(_counts(r.get("after")))}</p>'
                          + f'<p>{verdict_badge(r.get("verdict"))} {page.t(r.get("reason"))}</p>')
            else:
                proved = f'<p>{badge("unverified")} Not verified: no test run exists for this proposal.</p>'
            body += (f'<h3>{page.t(pid)}</h3><div class="compare">'
                     f'<div class="col ai"><h3>AI suggestion {badge("ai_generated")} not verified</h3>{ai}</div>'
                     f'<div class="col proved"><h3>What DepDoctor verified {badge("executed")}</h3>{proved}</div>'
                     f'</div>')
    rejected = [r for r in ((data["proposals"] or {}).get("rejected") or []) if isinstance(r, dict)]
    if rejected:
        body += (f'<details class="note"><summary>{len(rejected)} proposal(s) rejected by the validator</summary>'
                 '<ul class="tests">' + "".join(f"<li>{page.t(r.get('reason'), 300)}</li>" for r in rejected)
                 + "</ul></details>")
    return (f'<section class="card" id="ai" aria-label="AI suggestion versus verified result">'
            f'<h2>What the AI said vs what we proved</h2>{body}'
            f'<p class="caption">A suggestion becomes evidence only after the tests have run.</p></section>')


def _canary_card(canary, page):
    if canary is None:
        return (f'<div class="side tone-warn"><h3>Verifier self-check</h3><p><span class="verdict tone-warn">'
                f'! Not run</span> {badge("unverified")} canary.json was not provided.</p></div>')
    detected = canary.get("detected")
    if detected is None:
        return (f'<div class="side tone-warn"><h3>Verifier self-check</h3><p><span class="verdict tone-warn">'
                f'! Not run</span> {badge("unverified")}</p><p>{page.t(canary.get("reason"))}</p></div>')
    status = ('<span class="verdict tone-good">\u2713 Caught</span>' if detected
              else '<span class="verdict tone-bad">\u2717 MISSED</span>')
    tone = "good" if detected else "bad"
    missed = ("" if detected else '<p><b>The verifier missed a known-bad change, so the verdicts on this page are '
                                  'not confirmed.</b></p>')
    return (f'<div class="side tone-{tone}"><h3>Verifier self-check</h3>'
            f'<p>We removed <b>{page.t(canary.get("package"))}</b> from a sandbox copy. '
            f'A working verifier must flag this.</p>'
            f'<p>{status} {badge("executed")} verdict: {page.t(canary.get("verdict"))}</p>'
            f'<p class="meta">Before: {_counts_text(_counts(canary.get("before")))}<br>'
            f'After: {_counts_text(_counts(canary.get("after")))}</p>{missed}</div>')


def _chain(chain, page):
    parts = [p.strip() for p in str(chain).split("->")]
    return '<div class="chain">' + "<i>&rarr;</i>".join(f"<span>{page.t(p)}</span>" for p in parts) + "</div>"


def _strength_card(strength, page):
    head = f'<h3>Evidence strength {badge("observed")}</h3>'
    foot = ('<p class="note">Reach means a test imports a path to the changed code; it does not prove the changed '
            'lines ran.</p>')
    if strength is None:
        return (f'<div class="side tone-warn">{head}<p>{badge("unverified")} strength.json was not provided.</p>'
                f'{foot}</div>')
    rows, any_missing = [], False
    for p in strength.get("proposals") or []:
        pid = page.t(p.get("proposal_id"))
        if not p.get("reach"):
            any_missing = True
            rows.append(f'<p><b>{pid}</b>: {badge("unverified")} no changed package could be identified.</p>')
        for r in p.get("reach") or []:
            tests = r.get("tests_reaching") or []
            if tests and r.get("chain"):
                rows.append(f'<p><b>{pid}</b>: {len(tests)} test file(s) reach <b>{page.t(r.get("package"))}</b></p>'
                            + _chain(r["chain"], page))
            else:
                any_missing = True
                rows.append(f'<p><b>{pid}</b>: <span class="verdict tone-warn">! No test reaches this change: a '
                            f'green result is not evidence</span> ({page.t(r.get("package"))})</p>')
    if not rows:
        rows.append('<p class="empty">No verified proposal to check.</p>')
    tone = "warn" if any_missing or not strength.get("proposals") else "good"
    return f'<div class="side tone-{tone}">{head}{"".join(rows)}{foot}</div>'


def _trust(data, page):
    return (f'<section class="card" id="trust" aria-label="How much can you trust this">'
            f'<h2>How much can you trust this?</h2><div class="trust">'
            f'{_canary_card(data["canary"], page)}{_strength_card(data["strength"], page)}</div></section>')


def _timeline(run, page):
    steps = {s.get("step"): s for s in (run or {}).get("steps") or [] if isinstance(s, dict)}
    items = []
    for key, label, kind in TIMELINE:
        s = steps.get(key)
        if not s:
            continue
        prov, kind_text = STEP_KINDS[kind]
        failed = "" if s.get("ok", True) else ' <span class="failed">failed</span>'
        seconds = f'{s["seconds"]} s' if s.get("seconds") is not None else "time not recorded"
        items.append(f'<li><span class="dot {prov}" aria-hidden="true"></span><b>{escape(label)}</b> '
                     f'{escape(seconds)} <span class="kind">({escape(kind_text)})</span>{failed}</li>')
    if not items:
        return ""
    return (f'<section class="card" id="timeline" aria-label="Run timeline"><h2>What DepDoctor did</h2>'
            f'<ol class="steps">{"".join(items)}</ol></section>')


def _findings(evidence, page):
    if evidence is None:
        return (f'<section class="card" id="findings"><h2>Other findings (observed, not fixed)</h2>'
                f'<p>{badge("unverified")} evidence.json was not provided.</p></section>')
    rows = []
    for d in evidence.get("drift") or []:
        details = d.get("details") or {}
        rows.append(("drift", f'{d.get("package")}: ' + "; ".join(f"{f} {s!r}" if s is not None else f"{f} (absent)"
                                                                 for f, s in details.items()), ", ".join(details)))
    for u in evidence.get("undeclared") or []:
        rows.append(("undeclared import", f'{u.get("import")} is imported but not declared',
                     f'{u.get("file")}:{u.get("line")}'))
    for u in evidence.get("unused") or []:
        rows.append(("unused package", f'{u.get("package")} is declared but never imported', ""))
    for p in evidence.get("private_api") or []:
        rows.append(("private API", f'{p.get("module")}.{p.get("name")}', f'{p.get("file")}:{p.get("line")}'))

    def row(kind, text, where, prov="observed", extra=""):
        return (f'<div class="finding"><div><b>{escape(kind)}</b></div><div><p>{page.t(text)}</p>'
                + (f'<span class="where mono">{page.t(where)}</span>' if where else "")
                + f'</div><div>{badge(prov)}{extra}</div></div>')

    html = [row(*r) for r in rows]
    body = "".join(html[:FINDINGS_OPEN]) or '<p class="empty">No other findings.</p>'
    if len(html) > FINDINGS_OPEN:
        body += (f'<details><summary>Show {len(html) - FINDINGS_OPEN} more</summary>'
                 f'{"".join(html[FINDINGS_OPEN:])}</details>')
    hints = [row("hint", f'{c.get("package")}: {c.get("hint")}', "", "hint", " <span class=\"note\">not verified</span>")
             for c in evidence.get("candidates") or []]
    if hints:
        body += (f'<details><summary>{len(hints)} hint(s) from the built-in table (hint, not verified)</summary>'
                 f'{"".join(hints)}</details>')
    return (f'<section class="card" id="findings" aria-label="Other findings"><details open>'
            f'<summary><h2 style="display:inline">Other findings (observed, not fixed)</h2></summary>'
            f'<div class="findings">{body}</div></details></section>')


def _limits(sections, page):
    sec10 = next((s for s in sections if str(s.get("id")) == "10"), None)
    items = (sec10 or {}).get("items") or []
    return (f'<section class="card" id="limits" aria-label="Limits"><h2>Limits</h2>'
            f'{render_items(items, "No limits were recorded in the capsule.", page)}</section>')


# ---------------------------------------------------------------- page

def _normalise(data):
    data = data if isinstance(data, dict) else {}
    keys = ("baseline", "verify", "canary", "strength", "run", "proposals", "evidence")
    return {k: _dict(data.get(k)) for k in keys}


def _document(body):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Dependency Exit Capsule</title>
<style>{CSS}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def _fallback(capsule, sections):
    """No stage data: the capsule's own sections, with a note that nothing else was available."""
    model_line = _model_line(capsule)
    glance_section = next((s for s in sections if str(s.get("id")) == GLANCE_SECTION), None)
    glance_items = (glance_section or {}).get("items") or []
    glance = render_items(glance_items[:GLANCE_ITEMS], "No executed results in this capsule.")
    if len(glance_items) > GLANCE_ITEMS:
        glance += f'<p class="meta"><a href="#s{GLANCE_SECTION}">All executed results</a></p>'
    nav = "".join(f'<a href="#s{escape(str(s.get("id")))}">{escape(str(s.get("id")))}. '
                  f'{escape(str(s.get("title", "")))}</a> ' for s in sections)
    return _document(f"""<header>
<h1>Dependency Exit Capsule</h1>
<p class="meta">Generated: {escape(str(capsule.get("generated_at") or "unknown"))}</p>
<p class="meta">Model: {model_line or "not recorded"}</p>
</header>
<p class="card">{badge("unverified")} Stage files (baseline, verify, canary, strength, run) were not provided, so
this page shows the capsule's own sections only.</p>
<section class="card glance">
<h2>At a glance</h2>
{glance}
</section>
<nav aria-label="Sections">{nav}</nav>
{_full_report(sections, Page(), None)}
<footer>{_legend()}</footer>""")


def render(capsule, data=None):
    """Return the capsule as a complete HTML document (a string). `data` holds the parsed stage files."""
    sections = _sections(capsule)
    data = _normalise(data)
    if not any(data.values()):
        return _fallback(capsule, sections)
    root = (data["run"] or {}).get("repo") or ((data["evidence"] or {}).get("repo") or {}).get("path")
    page = Page(root)
    nav = ('<nav aria-label="Panels"><a href="#hero">Before &rarr; After</a><a href="#ai">AI vs proved</a>'
           '<a href="#trust">Trust</a><a href="#timeline">Timeline</a><a href="#findings">Other findings</a>'
           '<a href="#limits">Limits</a><a href="#full">Full report</a></nav>')
    return _document(
        _header(capsule, data, page) + nav + _hero(data, page) + _compare(data, page) + _trust(data, page)
        + _timeline(data["run"], page) + _findings(data["evidence"], page) + _limits(sections, page)
        + f'<footer>{_legend()}<section class="card" id="full"><h2>Full report (all ten capsule sections)</h2>'
          f'<p class="note">Section 8 is open; select a title to open the others.</p></section>'
        + _full_report(sections, page, {GLANCE_SECTION}) + "</footer>")


# ---------------------------------------------------------------- files

def load_stage_files(folder):
    """Parsed stage files found in `folder` (the capsule.json folder); None if there are none."""
    folder = Path(folder)
    data = {}
    for key, name in STAGE_FILES.items():
        data[key] = _read_json(folder / name)
    run = data["run"] or {}
    names = ["proposals.fallback.json", "proposals.json"] if str(run.get("origin", "")).startswith("fallback") \
        else ["proposals.json", "proposals.fallback.json"]
    data["proposals"] = next((p for p in (_read_json(folder / n) for n in names) if p), None)
    return data if any(data.values()) else None


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write(capsule, out_path, data=None):
    Path(out_path).write_text(render(capsule, data), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m tool.report_html",
                                     description="Render capsule.json as a self-contained HTML page")
    parser.add_argument("capsule", help="capsule.json written by the capsule step")
    parser.add_argument("--out", default="capsule.html", help="output file (default: capsule.html)")
    args = parser.parse_args(argv)
    capsule = _read_json(args.capsule)
    if capsule is None:
        print(f"error: could not read {args.capsule}", file=sys.stderr)
        return 2
    write(capsule, args.out, load_stage_files(Path(args.capsule).parent))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
