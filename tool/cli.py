"""Command line for DepDoctor: `python -m tool <command>`.

Each stage exposes a plain function; this file only wires arguments to it.
"""

import argparse
import sys


def cmd_scan(args):
    from .scan import write_evidence

    evidence = write_evidence(args.repo, args.out)
    counts = ", ".join(f"{key} {len(evidence[key])}" for key in
                       ("manifests", "drift", "usage", "undeclared", "unused", "private_api", "candidates"))
    print(f"wrote {args.out}: {counts}")
    return 0


def cmd_check_proposals(args):
    import json
    from pathlib import Path

    from .proposals import validate

    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    repo = args.repo or evidence["repo"]["path"]
    accepted, rejected = validate(Path(args.proposals).read_text(encoding="utf-8"), evidence, repo)
    for proposal in accepted:
        print(f"accepted {proposal['id']}")
    for item in rejected:
        print(f"rejected {item['id'] or '-'}: {item['reason']}")
    print(f"{len(accepted)} accepted, {len(rejected)} rejected")
    return 0 if accepted and not rejected else 1


def cmd_propose(args):
    from .proposals import propose

    result = propose(args.evidence, args.out, model=args.model, base_url=args.base_url, repo_path=args.repo,
                     baseline_path=args.baseline)
    print(f"wrote {args.out}: {len(result['proposals'])} accepted, {len(result['rejected'])} rejected, "
          f"{result['attempts']} attempt(s)")
    if result.get("error"):
        print(f"error: {result['error']}", file=sys.stderr)
    return 0 if result["proposals"] else 1


def cmd_verify(args):
    import json
    from pathlib import Path

    from . import verify

    proposals = json.loads(Path(args.proposals).read_text(encoding="utf-8"))
    before = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    result = verify.run(args.repo, proposals, before, args.workdir, python=args.python)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for r in result["results"]:
        print(f"{r['proposal_id']}: {r['verdict']} - {r['reason']}")
    print(f"wrote {args.out}: {len(result['results'])} proposal(s) verified")
    return 0


def cmd_capsule(args):
    from . import capsule

    inputs = (capsule.load_json(p) for p in (args.evidence, args.baseline, args.proposals, args.verify))
    result = capsule.build(*inputs, model_info={"name": args.model} if args.model else None)
    for path in capsule.write(result, args.out_dir):
        print(f"wrote {path}")
    return 0


def cmd_report(args):
    from . import capsule, report_html

    data = capsule.load_json(args.capsule)
    if data is None:
        raise ValueError(f"could not read {args.capsule}")
    report_html.write(data, args.out)
    print(f"wrote {args.out}")
    return 0


def cmd_analyze(args):
    from .finish import analyze

    result = analyze(args.repo, args.out_dir, python=args.python)
    return 0 if result["evidence"] is not None and result["baseline"] is not None else 1


def cmd_finish(args):
    from .finish import finish

    run = finish(args.repo, args.out_dir, python=args.python, model=args.model, base_url=args.base_url,
                 canary=not args.no_canary)
    return 0 if "capsule_json" in run["outputs"] else 1


def build_parser():
    from .proposals import DEFAULT_BASE_URL, DEFAULT_MODEL

    parser = argparse.ArgumentParser(prog="python -m tool", description="DepDoctor: evidence-based "
                                     "dependency maintenance for Python repos")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="read manifests and imports, write evidence.json")
    scan.add_argument("repo", help="path to the repo to scan")
    scan.add_argument("--out", default="evidence.json", help="output file (default: evidence.json)")
    scan.set_defaults(func=cmd_scan)

    check = sub.add_parser("check-proposals", help="validate proposals.json against the evidence and repo")
    check.add_argument("proposals", help="proposals.json to check")
    check.add_argument("--evidence", required=True, help="evidence.json written by scan")
    check.add_argument("--repo", help="repo path (default: repo.path from the evidence)")
    check.set_defaults(func=cmd_check_proposals)

    prop = sub.add_parser("propose", help="ask the local model for fixes, validate them, write proposals.json")
    prop.add_argument("evidence", help="evidence.json written by scan")
    prop.add_argument("--out", default="proposals.json", help="output file (default: proposals.json)")
    prop.add_argument("--model", default=DEFAULT_MODEL, help=f"model name (default: {DEFAULT_MODEL})")
    prop.add_argument("--base-url", default=DEFAULT_BASE_URL, help="local OpenAI-style endpoint "
                      f"(default: {DEFAULT_BASE_URL}); only localhost is allowed")
    prop.add_argument("--repo", help="repo path (default: repo.path from the evidence)")
    prop.add_argument("--baseline", help="baseline.json; its failing tests are put first in the prompt")
    prop.set_defaults(func=cmd_propose)

    ver = sub.add_parser("verify", help="apply each proposal to a sandbox copy, re-run the tests, compare")
    ver.add_argument("repo", help="path to the repo (never modified)")
    ver.add_argument("proposals", help="proposals.json (only accepted proposals are run)")
    ver.add_argument("baseline", help="baseline.json to compare against")
    ver.add_argument("--out", default="verify.json", help="output file (default: verify.json)")
    ver.add_argument("--workdir", default=".sandbox/verify", help="sandbox folder (default: .sandbox/verify)")
    ver.add_argument("--python", default="3.12", help="Python version for the test venv (default: 3.12)")
    ver.set_defaults(func=cmd_verify)

    cap = sub.add_parser("capsule", help="build capsule.json and capsule.md from the stage outputs")
    for name in ("evidence", "baseline", "proposals", "verify"):
        cap.add_argument(f"--{name}", help=f"{name}.json (missing inputs are reported as unverified)")
    cap.add_argument("--out-dir", default=".", help="folder for capsule.json and capsule.md (default: .)")
    cap.add_argument("--model", help="model name, if proposals.json does not record one")
    cap.set_defaults(func=cmd_capsule)

    rep = sub.add_parser("report", help="render capsule.json as a self-contained HTML page")
    rep.add_argument("capsule", help="capsule.json")
    rep.add_argument("--out", default="capsule.html", help="output file (default: capsule.html)")
    rep.set_defaults(func=cmd_report)

    ana = sub.add_parser("analyze", help="scan + baseline; print failing tests and valid evidence refs")
    ana.add_argument("repo", help="path to the repo (never modified)")
    ana.add_argument("--out-dir", default="out", help="output folder (default: out)")
    ana.add_argument("--python", default="3.12", help="Python version for the test venv (default: 3.12)")
    ana.set_defaults(func=cmd_analyze)

    fin = sub.add_parser("finish", help="validate or generate proposals, verify, write the capsule and run.json")
    fin.add_argument("repo", help="path to the repo (never modified)")
    fin.add_argument("--out-dir", default="out", help="output folder (default: out)")
    fin.add_argument("--python", default="3.12", help="Python version for the test venv (default: 3.12)")
    fin.add_argument("--model", default=DEFAULT_MODEL, help=f"model for the fallback call (default: {DEFAULT_MODEL})")
    fin.add_argument("--base-url", default=DEFAULT_BASE_URL, help="local model endpoint; only localhost is allowed")
    fin.add_argument("--no-canary", action="store_true",
                     help="skip the verifier self-check (a known-bad change that must be reported as regressed)")
    fin.set_defaults(func=cmd_finish)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
