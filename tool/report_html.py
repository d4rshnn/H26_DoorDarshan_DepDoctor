"""Render capsule.json as one self-contained HTML page (inline CSS, no scripts, no external files).

    py -3.12 -m tool.report_html capsule.json --out capsule.html

Every piece of capsule text is escaped with html.escape; nothing is parsed beyond display.
"""

import argparse
import json
import sys
from html import escape
from pathlib import Path

# label -> (mark, explanation). The mark and the label text tell them apart without colour.
PROVENANCE = {
    "observed": ("✓", "read from the repository files by deterministic code"),
    "executed": ("▶", "produced by running a command (install, pytest) and recording its output"),
    "ai_generated": ("✦", "written by the local model; trusted only where an executed result backs it"),
    "hint": ("ℹ", "from DepDoctor's built-in table of known migrations; a suggestion, not a finding"),
    "unverified": ("!", "not checked by any run; treat as an open question"),
}
GLANCE_SECTION = "8"
GLANCE_ITEMS = 3

CSS = """
:root {
  --bg: #f7f7f5; --card: #ffffff; --text: #1d1d1f; --muted: #5f6368; --border: #e2e2de;
  --pre-bg: #f1f1ee; --link: #1747a0;
  --observed-bg: #e3f4e8; --observed-fg: #17602f;
  --executed-bg: #e2edfb; --executed-fg: #1747a0;
  --ai_generated-bg: #efe6fb; --ai_generated-fg: #5b2a9e;
  --hint-bg: #ececec; --hint-fg: #444444;
  --unverified-bg: #fdf0d5; --unverified-fg: #7a4b00;
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
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: var(--link); }
main { max-width: 900px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.75rem; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 0 0 12px; }
.meta { color: var(--muted); font-size: .9rem; margin: 2px 0; }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px; margin: 16px 0;
}
.legend { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.legend li { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.legend .why { color: var(--muted); font-size: .9rem; }
.badge {
  display: inline-block; white-space: nowrap; font-size: .78rem; font-weight: 600;
  padding: 1px 8px; border-radius: 999px; border: 1px solid currentColor;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
""" + "".join(f".badge.{p} {{ background: var(--{p}-bg); color: var(--{p}-fg); }}\n" for p in PROVENANCE) + """
.badge.unknown { background: var(--hint-bg); color: var(--hint-fg); border-style: dashed; }
nav { font-size: .9rem; }
nav a { color: var(--muted); margin-right: 12px; white-space: nowrap; }
.items { list-style: none; margin: 0; padding: 0; }
.item { display: grid; grid-template-columns: 9.5rem 1fr; gap: 4px 12px; padding: 10px 0;
        border-top: 1px solid var(--border); }
.item:first-child { border-top: 0; padding-top: 0; }
.item .body { min-width: 0; }
.item p { margin: 0; overflow-wrap: anywhere; }
.source { color: var(--muted); font-size: .8rem; margin-top: 2px; overflow-wrap: anywhere;
          font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre {
  margin: 0; padding: 10px 12px; background: var(--pre-bg); border-radius: 6px; overflow-x: auto;
  font: .82rem/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre;
}
.empty { color: var(--muted); font-style: italic; margin: 0; }
.glance { border-width: 2px; }
@media (max-width: 600px) {
  .item { grid-template-columns: 1fr; }
  h1 { font-size: 1.45rem; }
}
@media print { nav { display: none; } .card { break-inside: avoid; } }
"""


def badge(provenance):
    label = str(provenance or "missing")
    if label in PROVENANCE:
        mark = PROVENANCE[label][0]
        return f'<span class="badge {label}">{mark} {escape(label)}</span>'
    return f'<span class="badge unknown">? {escape(label)}</span>'


def render_item(item):
    item = item if isinstance(item, dict) else {"text": item}
    text = str(item.get("text", ""))
    if "\n" in text or text.startswith("---"):
        body = f"<pre>{escape(text)}</pre>"
    else:
        body = f"<p>{escape(text)}</p>"
    source = item.get("source")
    if source:
        body += f'<div class="source">source: {escape(str(source))}</div>'
    return f'<li class="item"><div>{badge(item.get("provenance"))}</div><div class="body">{body}</div></li>'


def render_items(items, empty="No items in this section."):
    if not items:
        return f'<p class="empty">{escape(empty)}</p>'
    return '<ul class="items">' + "".join(render_item(i) for i in items) + "</ul>"


def _sort_key(section):
    sid = str(section.get("id", ""))
    return (0, int(sid), "") if sid.isdigit() else (1, 0, sid)


def render(capsule):
    """Return the capsule as a complete HTML document (a string)."""
    model = capsule.get("model") or {}
    sections = sorted((s for s in capsule.get("sections") or [] if isinstance(s, dict)), key=_sort_key)
    model_line = " · ".join(escape(str(model[k])) for k in ("name", "source", "agent", "license")
                                 if model.get(k))

    legend = "".join(f'<li>{badge(p)}<span class="why">{escape(why)}</span></li>'
                     for p, (_, why) in PROVENANCE.items())

    glance_section = next((s for s in sections if str(s.get("id")) == GLANCE_SECTION), None)
    glance_items = (glance_section or {}).get("items") or []
    glance = render_items(glance_items[:GLANCE_ITEMS], "No executed results in this capsule.")
    if len(glance_items) > GLANCE_ITEMS:
        glance += f'<p class="meta"><a href="#s{GLANCE_SECTION}">All executed results</a></p>'

    nav = "".join(f'<a href="#s{escape(str(s.get("id")))}">{escape(str(s.get("id")))}. '
                  f'{escape(str(s.get("title", "")))}</a> ' for s in sections)
    body = "".join(
        f'<section class="card" id="s{escape(str(s.get("id")))}">'
        f'<h2>{escape(str(s.get("id")))}. {escape(str(s.get("title", "")))}</h2>'
        f'{render_items(s.get("items") or [])}</section>'
        for s in sections)

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
<header>
<h1>Dependency Exit Capsule</h1>
<p class="meta">Generated: {escape(str(capsule.get("generated_at") or "unknown"))}</p>
<p class="meta">Model: {model_line or "not recorded"}</p>
</header>
<section class="card" aria-label="Provenance legend">
<h2>How each line is known</h2>
<ul class="legend">{legend}</ul>
</section>
<section class="card glance">
<h2>At a glance</h2>
{glance}
</section>
<nav aria-label="Sections">{nav}</nav>
{body}
</main>
</body>
</html>
"""


def write(capsule, out_path):
    Path(out_path).write_text(render(capsule), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m tool.report_html",
                                     description="Render capsule.json as a self-contained HTML page")
    parser.add_argument("capsule", help="capsule.json written by the capsule step")
    parser.add_argument("--out", default="capsule.html", help="output file (default: capsule.html)")
    args = parser.parse_args(argv)
    try:
        capsule = json.loads(Path(args.capsule).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: could not read {args.capsule}: {exc}", file=sys.stderr)
        return 2
    write(capsule, args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
