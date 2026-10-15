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


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m tool", description="DepDoctor: evidence-based "
                                     "dependency maintenance for Python repos")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="read manifests and imports, write evidence.json")
    scan.add_argument("repo", help="path to the repo to scan")
    scan.add_argument("--out", default="evidence.json", help="output file (default: evidence.json)")
    scan.set_defaults(func=cmd_scan)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
