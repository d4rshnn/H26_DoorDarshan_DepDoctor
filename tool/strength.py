"""Verification strength: does any test even import the code a proposal changes?

A green test run only confirms a change if some test reaches it. This is pure static analysis over
the repo's own import graph (every import statement, including imports inside functions); target
code is never executed. Importing a module is reach, not proof that a test exercises the changed
lines, so the result is a lower bound on what the test run can say, labelled `observed`.
"""

import ast
import re
from collections import deque
from pathlib import Path

from .scan import canonical, python_files

REQUIREMENT_NAME = re.compile(r"""^\s*["']?([A-Za-z0-9][A-Za-z0-9._-]*)""")


def is_test_file(rel):
    name = rel.rsplit("/", 1)[-1]
    return rel.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def module_map(root, files):
    """Dotted module name -> repo-relative file, for every .py file (src/ layout also mapped without src)."""
    modules = {}
    for path in files:
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        rel = path.relative_to(root).as_posix()
        modules[".".join(parts)] = rel
        if parts[0] == "src" and len(parts) > 1:
            modules.setdefault(".".join(parts[1:]), rel)
    return modules


def _package_of(rel):
    """Dotted package that relative imports in `rel` start from."""
    parts = rel[:-3].split("/")
    return parts[:-1]  # for pkg/__init__.py this is ["pkg"]; for pkg/mod.py also ["pkg"]


def _targets(module, names, modules):
    """Repo files reached by importing `module` (and, for from-imports, `module.name`)."""
    found, parts = [], module.split(".") if module else []
    for i in range(1, len(parts) + 1):  # importing a.b.c runs a/__init__, a/b/__init__, a/b/c
        rel = modules.get(".".join(parts[:i]))
        if rel:
            found.append(rel)
    for name in names:
        rel = modules.get(f"{module}.{name}" if module else name)
        if rel:
            found.append(rel)
    return found


def import_graph(root):
    """repo file -> set of repo files it imports (stdlib and third-party imports are ignored)."""
    root = Path(root)
    files = list(python_files(root))
    modules = module_map(root, files)
    graph = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        edges = set()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            graph[rel] = edges
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges.update(_targets(alias.name, [], modules))
            elif isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names if a.name != "*"]
                if node.level:
                    base = _package_of(rel)
                    base = base[:len(base) - (node.level - 1)] if node.level > 1 else base
                    module = ".".join(base + ([node.module] if node.module else []))
                else:
                    module = node.module or ""
                edges.update(_targets(module, names, modules))
        edges.discard(rel)
        graph[rel] = edges
    return graph


def _shortest_chain(graph, start, goals):
    """Shortest import path from start to any goal file, or None."""
    previous, queue = {start: None}, deque([start])
    while queue:
        node = queue.popleft()
        if node in goals:
            chain = []
            while node is not None:
                chain.append(node)
                node = previous[node]
            return chain[::-1]
        for nxt in sorted(graph.get(node, ())):
            if nxt not in previous:
                previous[nxt] = node
                queue.append(nxt)
    return None


def reach(repo_path, evidence, changed_packages):
    """For each package: which non-test files import it and which test files reach those files."""
    graph = import_graph(repo_path)
    tests = sorted(f for f in graph if is_test_file(f))
    usage = (evidence or {}).get("usage", [])
    result = {}
    for package in changed_packages:
        key = canonical(package)
        uses = [u for u in usage if canonical(u["package"]) == key and not is_test_file(u["file"])]
        sources = sorted({u["file"] for u in uses})
        import_of = {}
        for u in uses:
            import_of.setdefault(u["file"], u["import"])
        chains = {t: c for t in tests if (c := _shortest_chain(graph, t, set(sources)))}
        # example: prefer a real test module over conftest, then the shortest chain, then the name
        example = min(chains.items(), key=lambda kv: (kv[0].endswith("conftest.py"), len(kv[1]), kv[0]),
                      default=None)
        chain = " -> ".join(example[1] + [import_of[example[1][-1]]]) if example else None
        entry = {"package": package, "source_files": sources, "tests_reaching": sorted(chains), "chain": chain,
                 "strength": "tests_reach_change" if chains else "no_test_reaches_change", "provenance": "observed"}
        if not sources:
            entry["note"] = "no non-test file imports this package"
        result[package] = entry
    return result


def changed_packages(proposal, evidence):
    """Packages a proposal touches: names on edited requirement lines, else packages the edited file imports."""
    found = []
    usage = (evidence or {}).get("usage", [])
    declared = {canonical(m["package"]) for m in (evidence or {}).get("manifests", [])}
    for change in proposal.get("changes") or []:
        file = str(change.get("file", "")).replace("\\", "/")
        name = file.rsplit("/", 1)[-1]
        if re.fullmatch(r"requirements.*\.txt", name) or name == "pyproject.toml":
            for text in (change.get("old"), change.get("new")):
                m = REQUIREMENT_NAME.match(text or "")
                # in pyproject.toml only declared names count (not e.g. requires-python = ...)
                if m and (name != "pyproject.toml" or canonical(m.group(1)) in declared):
                    found.append(m.group(1))
        else:
            found.extend(u["package"] for u in usage if u["file"] == file)
    unique = {}
    for p in found:
        unique.setdefault(canonical(p), p)
    return list(unique.values())


def for_proposals(repo_path, evidence, proposals, verify_result):
    """strength.json content: reach per package for every verified proposal."""
    by_id = {p.get("id"): p for p in (proposals or {}).get("proposals", []) if isinstance(p, dict)}
    out = []
    for r in (verify_result or {}).get("results", []):
        packages = changed_packages(by_id.get(r.get("proposal_id")) or {}, evidence)
        out.append({"proposal_id": r.get("proposal_id"), "packages": packages,
                    "reach": list(reach(repo_path, evidence, packages).values()) if packages else []})
    return {"schema": 1, "provenance": "observed", "proposals": out}
