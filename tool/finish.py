"""One-command flow around the stages: `analyze` before the agent writes proposals, `finish` after.

    analyze: scan + baseline -> evidence.json, baseline.json; prints the facts the agent needs
    finish:  validate the agent's proposals.json (or fall back to a direct local-model call),
             verify them, write capsule.json / capsule.md / capsule.html and run.json

Every stage is passed in as a function so tests can run the whole flow without a venv or a model.
Expected problems (missing repo, failed install, no model, no proposals) are recorded, not raised.
"""

import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

from . import baseline as baseline_mod
from . import capsule as capsule_mod
from . import proposals as proposals_mod
from . import report_html, scan as scan_mod
from . import verify as verify_mod

ORIGIN_AGENT = "agent-written"
ORIGIN_FALLBACK = "fallback: direct local-model call"
ORIGIN_NONE = "none: no usable proposal"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _counts(run):
    pytest = (run or {}).get("pytest") or {}
    if pytest.get("passed") is None:
        return "tests did not run"
    return f"{pytest.get('passed')} passed, {pytest.get('failed')} failed, {pytest.get('errors')} errors"


class Steps:
    """Times each step and records the equivalent command, for run.json."""

    def __init__(self):
        self.items = []

    def run(self, name, command, fn, *args, **kwargs):
        start = time.perf_counter()
        step = {"step": name, "command": command, "ok": True}
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # recorded in run.json; the flow continues with what it has
            step.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            print(f"{name} failed: {exc}")
            return None
        finally:
            step["seconds"] = round(time.perf_counter() - start, 2)
            self.items.append(step)


# ---------------------------------------------------------------- analyze

def analyze(repo, out_dir, python="3.12", *, scan_fn=scan_mod.scan, baseline_fn=baseline_mod.run, steps=None):
    """Scan the repo and run its tests once; write evidence.json and baseline.json into out_dir.

    Returns {"evidence", "baseline", "refs", "steps"}; either result may be None if its step failed.
    """
    out, steps = Path(out_dir), steps or Steps()
    out.mkdir(parents=True, exist_ok=True)
    print(f"target: {Path(repo).resolve()}")

    evidence = steps.run("scan", f"python -m tool scan {repo} --out {out / 'evidence.json'}", scan_fn, repo)
    if evidence is not None:
        _write_json(out / "evidence.json", evidence)

    baseline = None
    if Path(repo).is_dir():
        workdir = out / ".sandbox" / "baseline"
        baseline = steps.run("baseline", f"baseline.run({repo}, python={python}, workdir={workdir})",
                             baseline_fn, repo, python=python, workdir=workdir)
    if baseline is not None:
        _write_json(out / "baseline.json", baseline)

    if baseline is not None:
        print(f"python: {baseline.get('python')}  install exit code: {(baseline.get('install') or {}).get('exit_code')}")
        print(f"tests: {_counts(baseline)}")
        failures = ((baseline.get("pytest") or {}).get("failures")) or []
        for f in failures:
            print(f"  FAILING {f.get('test')}: {f.get('first_error')}")
        if (baseline.get("pytest") or {}).get("skipped"):
            print(f"  tests skipped: {baseline['pytest']['skipped']}")
    else:
        print("tests: baseline did not run")

    refs = sorted(proposals_mod.evidence_refs(evidence)) if evidence else []
    print("valid evidence_refs (copy exactly):")
    for ref in refs:
        print(f"  {ref}")
    return {"evidence": evidence, "baseline": baseline, "refs": refs, "steps": steps}


# ---------------------------------------------------------------- finish

def _agent_proposals(path, evidence, repo):
    """Validate an agent-written proposals.json. Returns (usable dict or None, rejected list, note)."""
    if not path.is_file():
        return None, [], "no proposals.json in the output folder"
    raw = path.read_text(encoding="utf-8", errors="replace")
    accepted, rejected = proposals_mod.validate(raw, evidence, repo)
    for item in rejected:
        item["reason"] = f"agent-written proposals.json: {item['reason']}"
    if not accepted:
        return None, rejected, "agent-written proposals.json had no valid proposal"
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    model = data.get("model") if isinstance(data, dict) else None
    used = {"schema": 1, "model": model, "provenance": "ai_generated", "proposals": accepted, "rejected": rejected}
    return used, rejected, f"{len(accepted)} accepted, {len(rejected)} rejected"


def finish(repo, out_dir, python="3.12", *, model=proposals_mod.DEFAULT_MODEL,
           base_url=proposals_mod.DEFAULT_BASE_URL, scan_fn=scan_mod.scan, baseline_fn=baseline_mod.run,
           propose_fn=proposals_mod.propose, verify_fn=verify_mod.run, now=None):
    """Run the rest of the flow and write every output into out_dir. Returns the run.json dict."""
    started, start = _now(), time.perf_counter()
    out, steps = Path(out_dir), Steps()
    out.mkdir(parents=True, exist_ok=True)

    # 1. evidence and baseline
    evidence = capsule_mod.load_json(out / "evidence.json")
    baseline = capsule_mod.load_json(out / "baseline.json")
    if evidence is None or baseline is None:
        print("evidence.json or baseline.json missing: running analyze first")
        result = analyze(repo, out, python, scan_fn=scan_fn, baseline_fn=baseline_fn, steps=steps)
        evidence, baseline = result["evidence"], result["baseline"]

    # 2. proposals: the agent's file if it holds a valid proposal, else one direct local-model call
    agent_file = out / "proposals.json"
    used, agent_rejected, agent_note = (None, [], "no evidence, so proposals cannot be checked")
    if evidence is not None:
        used, agent_rejected, agent_note = _agent_proposals(agent_file, evidence, repo)
    print(f"agent proposals: {agent_note}")
    origin, proposals_file, model_source = ORIGIN_AGENT, agent_file, "named in the agent's proposals.json"

    if used is None and evidence is not None:
        origin, proposals_file = ORIGIN_FALLBACK, out / "proposals.fallback.json"
        baseline_path = out / "baseline.json" if baseline is not None else None
        used = steps.run("propose", f"python -m tool propose {out / 'evidence.json'} --out {proposals_file} "
                         f"--model {model}" + (f" --baseline {baseline_path}" if baseline_path else ""),
                         propose_fn, out / "evidence.json", proposals_file, model=model, base_url=base_url,
                         repo_path=repo, baseline_path=baseline_path)
        if used is not None:
            used = dict(used, rejected=agent_rejected + list(used.get("rejected") or []))
    if used is None:
        model_source = "configured model (--model); no proposals were produced"
    elif not used.get("model"):
        used["model"] = model
        model_source = "configured model (--model); the proposals file did not name one"
    elif origin == ORIGIN_FALLBACK:
        model_source = "model called directly by DepDoctor"
    accepted = (used or {}).get("proposals") or []
    if not accepted:
        origin = ORIGIN_NONE if origin == ORIGIN_AGENT else origin
    print(f"origin: {origin}; {len(accepted)} proposal(s) to verify")

    # 3. verify
    verify_result = {"schema": 1, "results": []}
    if accepted and baseline is not None:
        ran = steps.run("verify", f"python -m tool verify {repo} {proposals_file} {out / 'baseline.json'} "
                        f"--out {out / 'verify.json'} --workdir {out / '.sandbox' / 'verify'} --python {python}",
                        verify_fn, repo, used, baseline, out / ".sandbox" / "verify", python=python)
        verify_result = ran if ran is not None else verify_result
    _write_json(out / "verify.json", verify_result)

    # 4. capsule (json, md, html)
    agent = "Hermes Agent (Nous Research)" if origin == ORIGIN_AGENT else "none (direct call by DepDoctor)"
    model_info = {"name": (used or {}).get("model") or model, "agent": agent}
    paths = {"evidence": out / "evidence.json", "baseline": out / "baseline.json", "verify": out / "verify.json",
             "capsule_json": out / "capsule.json", "capsule_md": out / "capsule.md",
             "capsule_html": out / "capsule.html", "run": out / "run.json"}
    if used is not None and origin == ORIGIN_FALLBACK:
        paths["proposals"] = proposals_file
    elif used is not None:
        paths["proposals"] = agent_file
    capsule = steps.run("capsule", f"python -m tool capsule --out-dir {out}", capsule_mod.build,
                        evidence, baseline, used, verify_result, now=now, model_info=model_info)
    if capsule is not None:
        notes = [f"Proposal origin: {origin}.", f"Model name source: {model_source}."]
        if not accepted:
            notes.append("No usable proposal: nothing was applied or verified.")
        sec10 = next(s for s in capsule["sections"] if s["id"] == "10")
        sec10["items"].extend({"text": t, "provenance": "unverified", "source": "run.json"} for t in notes)
        steps.run("write capsule", f"capsule.write(..., {out})", capsule_mod.write, capsule, out)
        steps.run("report", f"python -m tool report {paths['capsule_json']} --out {paths['capsule_html']}",
                  report_html.write, capsule, paths["capsule_html"])

    # 5. run.json and summary
    run = {
        "schema": 1, "repo": str(Path(repo).resolve()), "out_dir": str(out.resolve()), "origin": origin,
        "agent_proposals": agent_note,
        "model": {"name": model_info["name"], "name_source": model_source, "agent": agent},
        "python": {"requested": python, "baseline": (baseline or {}).get("python"),
                   "tool": platform.python_version()},
        "started_at": started, "finished_at": _now(), "total_seconds": round(time.perf_counter() - start, 2),
        "steps": steps.items,
        "outputs": {k: str(v) for k, v in paths.items() if Path(v).is_file() or k == "run"},
    }
    _write_json(paths["run"], run)

    print("\nwrote:")
    for path in run["outputs"].values():
        print(f"  {path}")
    before = _counts(baseline)
    for r in verify_result["results"]:
        print(f"{r.get('proposal_id')}: {r.get('verdict')} (before {before}; after {_counts(r.get('after'))})")
    if not verify_result["results"]:
        print("no proposal was verified")
    return run
