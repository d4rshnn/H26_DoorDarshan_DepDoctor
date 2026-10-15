"""Build a provenance-labelled dependency maintenance report."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

LABELS = {"observed", "executed", "ai_generated", "hint", "unverified"}
SECTION_TITLES = [
    "Maintenance problem identified", "Repository and dependency details", "Risk indicators",
    "Usage locations", "AI-generated analysis and recommendations", "Proposed changes and maintenance approach",
    "Suggested tests and verification steps", "Executed test results", "Compatibility and maintenance gaps",
    "Unresolved concerns and limitations",
]
MODEL_DEFAULT = {"source": "Ollama, local", "agent": "Hermes Agent (Nous Research)", "license": "Apache-2.0"}
ABSOLUTE_PAREN_PATH = re.compile(r"\((?:[A-Za-z]:[\\/]|/)[^()]*\)")


def _omit_absolute_parenthesized_paths(text: str) -> str:
    return ABSOLUTE_PAREN_PATH.sub("(path omitted)", text)


def _install_summary(install: Any) -> str:
    install = install if isinstance(install, dict) else {}
    summary = f"Install exit code {install.get('exit_code', 'not recorded')}"
    output = install.get("output_tail")
    if not isinstance(output, str):
        return summary
    lines = output.splitlines()
    success = [line.strip() for line in lines if line.strip().startswith("Successfully installed")]
    last_nonempty = next((line.strip() for line in reversed(lines) if line.strip()), "")
    detail = success[-1] if success else last_nonempty
    if detail:
        summary += "; " + detail[:300]
    return summary


def _items(section: dict, entries: list[tuple[str, str, str]]) -> None:
    section["items"].extend({"text": str(t), "provenance": p, "source": s} for t, p, s in entries)


def _counts(data: Any) -> str:
    if not isinstance(data, dict):
        return "counts not available"
    return f"{data.get('passed', 0)} passed, {data.get('failed', 0)} failed, {data.get('errors', 0)} errors"


def _get(d: Any, *keys: str, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d: return default
        d = d[k]
    return d


def _canary_items(add, canary) -> None:
    """Section 8/9/10 items for the verifier self-check. canary=None (not passed) adds nothing."""
    if not isinstance(canary, dict):
        return
    detected = canary.get("detected")
    if detected is None:
        add(8, f"Verifier self-check (negative control) was not judged: {canary.get('reason', 'reason not recorded')}.", "unverified", "canary.json")
        add(10, "The verifier was not checked against a known-bad change in this run, so its verdicts are not calibrated.", "unverified", "canary.json")
        return
    add(8, f"Verifier self-check (negative control): removed {canary.get('package','?')} from requirements.txt in a sandbox copy; verdict {canary.get('verdict','not recorded')}; the verifier detected the breakage: {'yes' if detected else 'NO'} (before {_counts(canary.get('before'))}; after {_counts(canary.get('after'))}).", "executed", "canary.json")
    if not detected:
        add(9, f"WARNING: the verifier missed a known-bad change (removing {canary.get('package','?')} gave verdict {canary.get('verdict','not recorded')}, expected regressed). The verdicts above are not confirmed.", "executed", "canary.json")
        add(10, "The verifier missed a known-bad change, so a passing or improved verdict in this capsule may be wrong.", "unverified", "canary.json")


def _strength_items(add, strength) -> None:
    """Section 8/10 items saying whether any test reaches each proposal's change. None adds nothing."""
    if not isinstance(strength, dict):
        return
    unreached = []
    for p in strength.get("proposals", []) or []:
        pid = p.get("proposal_id", "proposal")
        if not p.get("reach"):
            add(8, f"Evidence strength for {pid}: no changed package could be identified, so test reach was not checked.", "unverified", "strength.json")
            continue
        for r in p["reach"]:
            if r.get("tests_reaching"):
                add(8, f"Evidence strength for {pid}: {len(r['tests_reaching'])} test file(s) reach {r.get('package','?')}: {r.get('chain')}", "observed", "strength.json")
            else:
                add(8, f"Evidence strength for {pid}: no test reaches {r.get('package','?')}: the test result above cannot confirm this change", "observed", "strength.json")
                unreached.append(f"{pid} ({r.get('package','?')})")
    if unreached:
        add(10, f"No test imports the code changed by {', '.join(unreached)}; a passing run does not confirm those changes.", "unverified", "strength.json")


def build(evidence, baseline, proposals, verify, *, now=None, model_info=None, canary=None, strength=None) -> dict:
    evidence = evidence if isinstance(evidence, dict) else None
    baseline = baseline if isinstance(baseline, dict) else None
    proposals = proposals if isinstance(proposals, dict) else None
    verify = verify if isinstance(verify, dict) else None
    model_name = (proposals or {}).get("model") or ((model_info or {}).get("name") if isinstance(model_info, dict) else None)
    model = {**MODEL_DEFAULT, **(model_info if isinstance(model_info, dict) else {})}
    model["name"] = model_name or "unknown (not recorded)"
    if not model_name: model.setdefault("_missing", True)
    stamp = now if now is not None else datetime.now(timezone.utc)
    if isinstance(stamp, datetime):
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    sections = [{"id": str(i), "title": title, "items": []} for i, title in enumerate(SECTION_TITLES, 1)]
    s = sections
    def add(n, text, prov, source): s[n-1]["items"].append({"text": _omit_absolute_parenthesized_paths(str(text)), "provenance": prov, "source": source})
    if not model_name: add(5, "Model name was not recorded in proposals or model_info.", "unverified", "proposals.json#model")
    for name, value in (("evidence", evidence), ("baseline", baseline), ("proposals", proposals), ("verify", verify)):
        if value is None: add(10, f"Input {name} is missing or invalid.", "unverified", f"{name}.json")

    pytest = _get(baseline, "pytest", default={})
    if baseline:
        add(1, f"{_counts(pytest)} on Python {_get(baseline,'python',default='version not recorded')} before any change.", "executed", "baseline.json#pytest")
        for i, f in enumerate(pytest.get("failures", []) or []): add(1, f"{f.get('test','test name not recorded')}: {f.get('first_error','error not recorded')}", "executed", f"baseline.json#pytest.failures[{i}]")
    if evidence:
        for i, d in enumerate(evidence.get("drift", []) or []): add(1, f"{d.get('package','package')}: differing specifiers {json.dumps(d.get('details',{}), ensure_ascii=False)}", "observed", f"evidence.json#drift[{i}]")
        for i, d in enumerate(evidence.get("undeclared", []) or []): add(1, f"Undeclared import {d.get('import','?')} at {d.get('file','?')}:{d.get('line','?')}", "observed", f"evidence.json#undeclared[{i}]")
        for i, d in enumerate(evidence.get("unused", []) or []): add(1, f"Unused package: {d.get('package','?')}", "observed", f"evidence.json#unused[{i}]")
    if not s[0]["items"]: add(1, "not available", "unverified", "inputs")

    if evidence:
        repo=evidence.get("repo",{}) or {}
        for key,label in (("commit","repo commit"),("python_requires","python_requires")):
            if repo.get(key) is not None: add(2, f"{label}: {repo[key]}", "observed", f"evidence.json#repo.{key}")
        versions=repo.get("python_mentioned")
        add(2, f"Python versions mentioned in README/Dockerfile: {', '.join(map(str,versions)) if versions else 'not recorded'}", "observed" if versions else "unverified", "evidence.json#repo.python_mentioned")
        counts=defaultdict(int)
        for x in evidence.get("manifests",[]) or []: counts[x.get("file","unknown manifest")]+=1
        for f,c in counts.items(): add(2, f"{f}: {c} declared package entries", "observed", "evidence.json#manifests")
    if baseline:
        add(2, f"Baseline used Python {_get(baseline,'python',default='version not recorded')}.", "executed", "baseline.json#python")
        ins=_get(baseline,"install",default={}) or {}
        add(2, _install_summary(ins), "executed", "baseline.json#install")
        add(2, f"pip check: {_get(baseline,'pip_check',default='not recorded')}", "executed", "baseline.json#pip_check")
    if not s[1]["items"]: add(2,"not available","unverified","inputs")

    if evidence:
        for i,d in enumerate(evidence.get("drift",[]) or []): add(3,f"Drift: {d.get('package','?')} {json.dumps(d.get('details',{}),ensure_ascii=False)}","observed",f"evidence.json#drift[{i}]")
        for i,d in enumerate(evidence.get("private_api",[]) or []): add(3,f"Private API: {d.get('module','?')}.{d.get('name','?')} at {d.get('file','?')}:{d.get('line','?')}","observed",f"evidence.json#private_api[{i}]")
        for key,desc in (("undeclared","Undeclared"),("unused","Unused")):
            for i,d in enumerate(evidence.get(key,[]) or []):
                where = f" at {d['file']}:{d['line']}" if d.get("file") is not None and d.get("line") is not None else ""
                add(3,f"{desc}: {d.get('import',d.get('package','?'))}{where}","observed",f"evidence.json#{key}[{i}]")
        for i,d in enumerate(evidence.get("candidates",[]) or []): add(3,f"Hint (not verified): {d.get('package','?')}: {d.get('hint','')}","hint",f"evidence.json#candidates[{i}]")
    if not s[2]["items"]: add(3,"none found","observed","evidence.json")

    if evidence:
        grouped=defaultdict(list)
        for u in evidence.get("usage",[]) or []: grouped[u.get("package","unknown")].append(f"{u.get('file','?')}:{u.get('line','?')} ({', '.join(u.get('names',[]) or [])})")
        for pkg,locs in grouped.items(): add(4,f"{pkg}: {'; '.join(locs)}","observed","evidence.json#usage")
    if not s[3]["items"]: add(4,"none found","observed","evidence.json#usage")

    props=(proposals or {}).get("proposals",[]) or []
    if proposals:
        for i,p in enumerate(props):
            attempts = f"; attempts: {proposals['attempts']}" if "attempts" in proposals else ""
            add(5,f"{p.get('problem','Problem not recorded')}. Rationale: {p.get('rationale','not recorded')} Model: {model['name']}{attempts}.","ai_generated",f"proposals.json#proposals[{i}]")
        for i,r in enumerate(proposals.get("rejected",[]) or []): add(5,f"Validator rejected proposal: {r.get('reason','reason not recorded')}","executed",f"proposals.json#rejected[{i}]")
        if not props and not proposals.get("rejected"): add(5,"No proposals were recorded.","unverified","proposals.json#proposals")
        if proposals.get("error"): add(5,f"Proposal generation reported an error: {proposals['error']}","unverified","proposals.json#error")
    if not s[4]["items"]: add(5,"Proposal input was empty or unavailable.","unverified","proposals.json")

    if proposals:
        for i,p in enumerate(props):
            for c in p.get("changes",[]) or []: add(6,f"{c.get('file','?')} — {c.get('action','change')}: {c.get('old','?')} -> {c.get('new','?')}. AI-stated risk: {p.get('risk','not recorded')}","ai_generated",f"proposals.json#proposals[{i}]")
    if verify:
        for i,r in enumerate(verify.get("results",[]) or []):
            if r.get("apply_ok"): add(6,f"Applied diff for {r.get('proposal_id','proposal')}:","executed",f"verify.json#results[{i}]")
            if r.get("apply_ok"): s[5]["items"][-1]["diff"] = r.get("applied_diff","")
    if not s[5]["items"]: add(6,"not available","unverified","inputs")

    failures=(pytest.get("failures",[]) or []) if isinstance(pytest,dict) else []
    for f in failures: add(7,f"py -3.12 -m pytest {f.get('test','<failing test id>')}","hint","baseline.json#pytest.failures")
    if baseline: add(7,"py -3.12 -m pip check","hint","baseline.json#pip_check")
    for i,p in enumerate(props): add(7,f"AI-stated risk for {p.get('id','proposal')}: {p.get('risk','not recorded')}","ai_generated",f"proposals.json#proposals[{i}]")
    if not s[6]["items"]: add(7,"not available","unverified","inputs")

    if baseline: add(8,f"Before: {_counts(pytest)}.","executed","baseline.json#pytest")
    results=(verify or {}).get("results",[]) or []
    for i,r in enumerate(results):
        after=_get(r,"after","pytest",default={}) or {}
        reason = f"; reason={r['reason']}" if r.get("reason") else ""
        add(8,f"{r.get('proposal_id','proposal')}: apply_ok={r.get('apply_ok','not recorded')}; before {_counts(pytest)}; after {_counts(after)}; fixed_tests={r.get('fixed_tests',[])}; newly_broken_tests={r.get('newly_broken_tests',[])}; verdict={r.get('verdict','not recorded')}{reason}","executed",f"verify.json#results[{i}]")
        if r.get("verdict")=="could_not_run": add(8,f"{r.get('proposal_id','proposal')} could not be run.","unverified",f"verify.json#results[{i}]")
    if not verify: add(8,"Verify results are missing.","unverified","verify.json")
    _strength_items(add, strength)
    _canary_items(add, canary)
    if not s[7]["items"]: add(8,"not available","unverified","inputs")

    for i,r in enumerate(results):
        if r.get("verdict") in ("regressed","unchanged"):
            broken=len(r.get("newly_broken_tests",[]) or [])
            add(9,f"Proposal {r.get('proposal_id','?')} was {r['verdict']} (apply_ok={r.get('apply_ok','unknown')}, {broken} newly broken tests).","executed",f"verify.json#results[{i}]")
    best=next((r for r in results if r.get("verdict")=="improved"),None)
    if best:
        aft=_get(best,"after","pytest",default={}) or {}
        for f in aft.get("failures",[]) or []: add(9,f"Still failing after best proposal {best.get('proposal_id','?')}: {f.get('test','?')} — {f.get('first_error','error not recorded')}","executed","verify.json#results")
    if evidence:
        addressed=set()
        for r in results:
            if r.get("verdict")=="improved" and r.get("apply_ok"): addressed.add(r.get("proposal_id"))
        for i,d in enumerate(evidence.get("drift",[]) or []): add(9,f"Still open: drift in {d.get('package','?')}.","observed",f"evidence.json#drift[{i}]")
        for i,d in enumerate(evidence.get("private_api",[]) or []): add(9,f"Still open: private API {d.get('module','?')}.{d.get('name','?')} at {d.get('file','?')}:{d.get('line','?')}.","observed",f"evidence.json#private_api[{i}]")
    if not s[8]["items"]: add(9,"none found","observed","inputs")

    pyver=_get(baseline,"python",default="version was not recorded")
    for t in ("Only the target's own tests were run; their coverage of the changed code is unknown.",f"Only Python {pyver} was tested.","AI proposals can be wrong; only the executed results above are evidence.","Unparseable Python files, if any, were skipped by the scanner and are not listed."):
        add(10,t,"unverified","capsule limitations")
    for i,r in enumerate((proposals or {}).get("rejected",[]) or []): add(10,f"Proposal rejection: {r.get('reason','reason not recorded')}","unverified",f"proposals.json#rejected[{i}]")
    for i,r in enumerate(results):
        if r.get("verdict")=="could_not_run": add(10,f"Proposal {r.get('proposal_id','?')} could not be run.","unverified",f"verify.json#results[{i}]")
    if not s[9]["items"]: add(10,"not available","unverified","inputs")
    capsule={"schema":1,"generated_at":stamp,"model":{k:v for k,v in model.items() if k!="_missing"},"sections":sections}
    problems=validate_capsule(capsule)
    if problems: raise ValueError("Invalid capsule: "+"; ".join(problems))
    return capsule


def validate_capsule(capsule) -> list[str]:
    problems=[]
    if not isinstance(capsule,dict): return ["capsule must be an object"]
    sections=capsule.get("sections")
    if not isinstance(sections,list) or len(sections)!=10: return ["capsule must contain ten sections"]
    for i,section in enumerate(sections):
        if not isinstance(section,dict) or not section.get("id") or not section.get("title") or not isinstance(section.get("items"),list):
            problems.append(f"section {i+1} is missing required fields"); continue
        if not section["items"]: problems.append(f"section {i+1} is empty")
        for j,item in enumerate(section["items"]):
            if not isinstance(item,dict) or any(k not in item for k in ("text","provenance","source")):
                problems.append(f"section {i+1} item {j+1} is missing a field"); continue
            if item["provenance"] not in LABELS: problems.append(f"section {i+1} item {j+1} has unknown provenance")
    return problems


def render_markdown(capsule) -> str:
    lines=["# Dependency Exit Capsule", "", f"Generated: {capsule['generated_at']}", f"Model: {capsule['model']['name']}", "", "## Legend", "", "- [observed] Read directly from repository files.", "- [executed] Produced by a command that was run.", "- [ai_generated] Suggested by the AI model; not verified by that label.", "- [hint] Built-in knowledge lead; not verified.", "- [unverified] Could not be checked or input is missing.", "", "## At a glance", ""]
    sections = {sec["id"]: sec for sec in capsule["sections"]}
    before_items = sections.get("1", {}).get("items", [])
    before_line = next((i["text"] for i in before_items if i["provenance"] == "executed"), "")
    match = re.search(r"(\d+ passed, \d+ failed, \d+ errors) on Python (.+?) before any change", before_line)
    if match:
        lines.append(f"- Before: {match.group(1)} (Python {match.group(2)})")
    else:
        lines.append("- Before: unavailable (Python version not recorded)")
    result_items = sections.get("8", {}).get("items", [])
    any_result = False
    for item in result_items:
        if item["provenance"] != "executed" or item["text"].startswith("Before:"):
            continue
        result = re.match(r"([^:]+): .*?after (\d+ passed, \d+ failed, \d+ errors).*?verdict=([^;]+)", item["text"])
        if result:
            lines.append(f"- {result.group(1)}: {result.group(3)} (after: {result.group(2)})")
            any_result = True
    if any(i["provenance"] == "unverified" and "Verify results are missing" in i["text"] for i in result_items):
        lines.append("- Verify: missing")
    elif not any_result:
        lines.append("- Verify: no proposal results")
    lines += ["", "## Sections", ""]
    for sec in capsule["sections"]:
        lines += [f"## {sec['id']}. {sec['title']}", ""]
        for item in sec["items"]:
            text=_omit_absolute_parenthesized_paths(item["text"]).replace("\n","\n  ")
            lines.append(f"- [{item['provenance']}] {text}")
            if "diff" in item: lines.extend(["", "  ```diff", item["diff"].rstrip(), "  ```"])
        lines.append("")
    return "\n".join(lines).rstrip()+"\n"


def write(capsule, out_dir) -> tuple[str,str]:
    path=Path(out_dir); path.mkdir(parents=True,exist_ok=True)
    jp=path/"capsule.json"; mp=path/"capsule.md"
    jp.write_text(json.dumps(capsule,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    mp.write_text(render_markdown(capsule),encoding="utf-8")
    return str(jp),str(mp)


def load_json(path) -> dict | None:
    if path is None: return None
    try:
        value=json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError,UnicodeError,TypeError): return None


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("evidence","baseline","proposals","verify"): parser.add_argument("--"+name)
    parser.add_argument("--out-dir",default="."); parser.add_argument("--model")
    args=parser.parse_args(argv)
    mi={"name":args.model} if args.model else None
    capsule=build(*(load_json(getattr(args,n)) for n in ("evidence","baseline","proposals","verify")),model_info=mi)
    write(capsule,args.out_dir)


if __name__ == "__main__": main()
