"""Stage 1: read a Python repo and write evidence.json.

Deterministic and offline: no network, no AI, nothing is installed or imported from the
target. Every item is either `observed` (read from a file) or `hint` (from the small
built-in table of known migrations in CANDIDATES).
"""

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

SCHEMA = 1
OBSERVED = "observed"
HINT = "hint"

SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", ".sandbox",
             ".tox", ".nox", ".mypy_cache", ".pytest_cache", "node_modules", "build", "dist",
             "site-packages"}

# Runtime manifests: a package declared in one of these but not the other is drift.
RUNTIME_FILES = ("pyproject.toml", "requirements.txt")

# Import name -> distribution name, for imports whose name differs from the package.
IMPORT_TO_DIST = {
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "git": "GitPython",
    "jwt": "PyJWT",
    "magic": "python-magic",
    "OpenSSL": "pyOpenSSL",
    "PIL": "Pillow",
    "pkg_resources": "setuptools",
    "serial": "pyserial",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "zmq": "pyzmq",
}

# Packages normally run as commands, not imported; never reported as unused.
TOOL_PACKAGES = {"black", "build", "coverage", "flake8", "isort", "mypy", "nose", "pip",
                 "pre-commit", "pylint", "pytest", "pytest-cov", "ruff", "setuptools", "tox",
                 "twine", "wheel"}

PYTHON_MENTION = re.compile(r"python[\s:\-]?v?(3\.\d{1,2})(?![\d.]*\d)", re.IGNORECASE)
REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def canonical(name):
    """PEP 503 normalised name, used only for comparing packages."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------- manifests

def parse_requirement(text):
    """Split 'Name[extra]>=1 ; marker' into (name, specifier). None if not a requirement."""
    text = text.split(" #", 1)[0].split(";", 1)[0].strip()
    if not text or text.startswith(("#", "-")) or "://" in text.split("@", 1)[0]:
        return None
    m = REQUIREMENT.match(text)
    if not m:
        return None
    return m.group(1), re.sub(r"\s+", "", m.group(3))


def read_requirements(path, rel):
    items = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        parsed = parse_requirement(line)
        if parsed:
            items.append({"file": rel, "package": parsed[0], "specifier": parsed[1],
                          "line": lineno, "provenance": OBSERVED})
    return items


def read_pyproject(path):
    """Return (manifest items, requires-python) from [project]."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        project = tomllib.loads(text).get("project", {})
    except tomllib.TOMLDecodeError as exc:
        print(f"scan: could not parse pyproject.toml: {exc}", file=sys.stderr)
        return [], None
    deps = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        deps.extend(extra)
    lines = text.splitlines()
    items, start = [], 0
    for dep in deps:
        parsed = parse_requirement(dep)
        if not parsed:
            continue
        lineno = None
        for i in range(start, len(lines)):
            if f'"{dep}"' in lines[i] or f"'{dep}'" in lines[i]:
                lineno, start = i + 1, i + 1
                break
        items.append({"file": "pyproject.toml", "package": parsed[0], "specifier": parsed[1],
                      "line": lineno, "provenance": OBSERVED})
    return items, project.get("requires-python")


def read_manifests(root):
    items, requires_python = [], None
    if (root / "pyproject.toml").is_file():
        items, requires_python = read_pyproject(root / "pyproject.toml")
    for path in sorted(root.glob("requirements*.txt")):
        items.extend(read_requirements(path, path.name))
    return items, requires_python


# ---------------------------------------------------------------- python versions

def python_mentions(root):
    """Python 3.x versions named in README.md and Dockerfile: {file: [versions]}."""
    found = {}
    for name in ("README.md", "Dockerfile"):
        path = root / name
        if path.is_file():
            versions = sorted(set(PYTHON_MENTION.findall(path.read_text(encoding="utf-8", errors="replace"))),
                              key=_version)
            if versions:
                found[name] = versions
    return found


def _version(text):
    return tuple(int(p) for p in re.findall(r"\d+", text))


def satisfies(version, spec):
    """True if version '3.12' meets a requires-python spec like '>=3.10,<4'."""
    v = _version(version)
    for clause in filter(None, (c.strip() for c in spec.split(","))):
        m = re.match(r"(~=|==|!=|<=|>=|<|>)\s*([\d.*]+)", clause)
        if not m:
            continue
        op, target = m.groups()
        if target.endswith(".*"):
            prefix = _version(target)
            match = v[:len(prefix)] == prefix
            if (op == "==") != match:
                return False
            continue
        t = _version(target)
        n = max(len(v), len(t))
        a, b = v + (0,) * (n - len(v)), t + (0,) * (n - len(t))
        ok = {"==": a == b, "!=": a != b, "<=": a <= b, ">=": a >= b, "<": a < b, ">": a > b,
              "~=": a >= b and v[:len(t) - 1] == t[:len(t) - 1]}[op]
        if not ok:
            return False
    return True


# ---------------------------------------------------------------- drift

def find_drift(manifests, requires_python, mentions):
    by_pkg = {}
    for item in manifests:
        by_pkg.setdefault(canonical(item["package"]), {}).setdefault(item["file"], item)

    present_runtime = {f for f in RUNTIME_FILES if any(i["file"] == f for i in manifests)}
    drift = []
    for files in by_pkg.values():
        name = next(iter(files.values()))["package"]
        specs = {f: i["specifier"] for f, i in files.items()}
        missing = [f for f in present_runtime if f not in files] if len(present_runtime) == 2 else []
        if len(set(specs.values())) > 1 or (missing and len(missing) < len(present_runtime)):
            details = dict(specs)
            for f in missing:
                details[f] = None
            drift.append({"package": name, "details": details, "provenance": OBSERVED})

    if requires_python and mentions:
        all_versions = {v for vs in mentions.values() for v in vs}
        disagree = len({tuple(vs) for vs in mentions.values()}) > 1
        if disagree or not all(satisfies(v, requires_python) for v in all_versions):
            details = {"pyproject.toml": requires_python}
            details.update({f: ", ".join(vs) for f, vs in mentions.items()})
            drift.append({"package": "python", "details": details, "provenance": OBSERVED})
    return drift


# ---------------------------------------------------------------- imports

def python_files(root):
    for path in sorted(root.rglob("*.py")):
        if not SKIP_DIRS.intersection(path.relative_to(root).parts[:-1]):
            yield path


def local_modules(root, files):
    """Top-level names that resolve to the repo itself (packages, modules, src/ layout)."""
    names = set()
    for path in files:
        parts = path.relative_to(root).parts
        names.update(parts[:-1])
        names.add(path.stem)
    return names


def collect_imports(root, files):
    """Yield (rel_file, line, module, names, node) for each absolute import statement."""
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
        except SyntaxError as exc:
            print(f"scan: skipped {rel} (could not parse: {exc.msg})", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield rel, node.lineno, alias.name, [alias.name], tree
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                yield rel, node.lineno, node.module, [a.name for a in node.names], tree


def resolve_dist(top, declared):
    """Distribution name for a top-level import, preferring the spelling in the manifests."""
    if canonical(top) in declared:
        return declared[canonical(top)]
    dist = IMPORT_TO_DIST.get(top, top)
    return declared.get(canonical(dist), dist)


def scan_imports(root, declared):
    files = list(python_files(root))
    local = local_modules(root, files)
    stdlib = set(sys.stdlib_module_names)
    usage, undeclared, private_api, pydantic_v1 = [], [], [], []
    for rel, line, module, names, tree in collect_imports(root, files):
        top = module.split(".", 1)[0]
        if top in stdlib or top == "__future__" or (top in local and canonical(top) not in declared):
            continue
        dist = resolve_dist(top, declared)
        usage.append({"package": dist, "import": module, "file": rel, "line": line,
                      "names": names, "provenance": OBSERVED})
        if canonical(dist) not in declared:
            undeclared.append({"import": module, "file": rel, "line": line, "provenance": OBSERVED})
        private = [p for p in module.split(".")[1:] if _is_private(p)]
        if private:
            private_api.append({"module": module, "name": private[0], "file": rel, "line": line,
                                "provenance": OBSERVED})
        elif names != [module]:
            for name in names:
                if _is_private(name):
                    private_api.append({"module": module, "name": name, "file": rel, "line": line,
                                        "provenance": OBSERVED})
        if top == "pydantic" and _uses_pydantic_v1(names, tree):
            pydantic_v1.append(rel)
    return usage, undeclared, private_api, sorted(set(pydantic_v1))


def _is_private(name):
    return name.startswith("_") and not name.startswith("__")


PYDANTIC_V1_NAMES = {"validator", "root_validator", "parse_obj_as", "BaseSettings"}


def _uses_pydantic_v1(names, tree):
    if PYDANTIC_V1_NAMES.intersection(names):
        return True
    return any(isinstance(n, ast.ClassDef) and any(isinstance(b, ast.ClassDef) and b.name == "Config"
                                                   for b in n.body)
               for n in ast.walk(tree))


# ---------------------------------------------------------------- unused

def find_unused(manifests, usage):
    used = {canonical(u["package"]) for u in usage}
    unused, seen = [], set()
    for item in manifests:
        key = canonical(item["package"])
        if key in used or key in TOOL_PACKAGES or key in seen:
            continue
        seen.add(key)
        unused.append({"package": item["package"], "provenance": OBSERVED})
    return unused


# ---------------------------------------------------------------- candidates

def find_candidates(manifests, usage, pydantic_v1_files):
    declared = {canonical(i["package"]): i for i in manifests}
    imported = {u["import"].split(".", 1)[0] for u in usage}
    found = []

    def add(package, hint):
        found.append({"id": f"c{len(found) + 1}", "package": package, "hint": hint, "provenance": HINT})

    if "toml" in declared or "toml" in imported:
        add("toml", "tomllib is in the standard library from Python 3.11")
    if "appdirs" in declared or "appdirs" in imported:
        add("appdirs", "appdirs is no longer maintained; platformdirs is its maintained successor")
    if "pkg_resources" in imported:
        add("setuptools", "pkg_resources is deprecated and removed from recent setuptools; "
                          "use importlib.resources (files) and importlib.metadata instead")
    jinja = [i for i in manifests if canonical(i["package"]) == "jinja2"]
    if any(_pins_major(i["specifier"], 2) for i in jinja):
        add(jinja[0]["package"], "Jinja2 2.x imports markupsafe.soft_unicode, removed in MarkupSafe 2.1; "
                                 "Jinja2 3.x does not")
    if pydantic_v1_files:
        name = declared["pydantic"]["package"] if "pydantic" in declared else "pydantic"
        add(name, "pydantic v1 API (validator, root_validator, class Config) is used in "
                  + ", ".join(pydantic_v1_files)
                  + "; pydantic 2 replaces it with field_validator, model_validator and model_config")
    if "nose" in declared:
        add(declared["nose"]["package"], "nose is unmaintained and imports imp, removed in Python 3.12; "
                                         "run the tests with pytest instead")
    return found


def _pins_major(spec, major):
    m = re.search(r"(?:==|~=|<=?|>=?)\s*(\d+)", spec)
    return bool(m) and int(m.group(1)) == major and not spec.startswith(">")


# ---------------------------------------------------------------- repo info

def git_commit(root):
    """Short commit id read from .git files (no git command is run)."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        if (git / ref).is_file():
            return (git / ref).read_text().strip()[:7]
        for line in (git / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0][:7]
    except OSError:
        pass
    return None


def scan(repo_path):
    root = Path(repo_path)
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {repo_path}")
    manifests, requires_python = read_manifests(root)
    declared = {}
    for item in manifests:
        declared.setdefault(canonical(item["package"]), item["package"])
    mentions = python_mentions(root)
    usage, undeclared, private_api, pydantic_v1 = scan_imports(root, declared)
    return {
        "schema": SCHEMA,
        "repo": {
            "path": str(repo_path),
            "commit": git_commit(root),
            "python_requires": requires_python,
            "python_mentioned": sorted({v for vs in mentions.values() for v in vs}, key=_version),
        },
        "manifests": manifests,
        "drift": find_drift(manifests, requires_python, mentions),
        "usage": usage,
        "undeclared": undeclared,
        "unused": find_unused(manifests, usage),
        "private_api": private_api,
        "candidates": find_candidates(manifests, usage, pydantic_v1),
    }


def write_evidence(repo_path, out_path):
    evidence = scan(repo_path)
    Path(out_path).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence
