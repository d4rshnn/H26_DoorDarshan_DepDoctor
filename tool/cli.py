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

    result = propose(args.evidence, args.out, model=args.model, base_url=args.base_url, repo_path=args.repo)
    print(f"wrote {args.out}: {len(result['proposals'])} accepted, {len(result['rejected'])} rejected, "
          f"{result['attempts']} attempt(s)")
    if result.get("error"):
        print(f"error: {result['error']}", file=sys.stderr)
    return 0 if result["proposals"] else 1


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
    prop.set_defaults(func=cmd_propose)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
