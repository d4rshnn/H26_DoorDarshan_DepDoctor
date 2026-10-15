import json
import shutil
from pathlib import Path

import pytest

from tool import scan
from tool.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "evidence.json").read_text(encoding="utf-8"))

PYPROJECT = """\
[project]
name = "tiny"
requires-python = ">=3.10"
dependencies = [
    "PyYAML>=6",
    "requests==2.31.0",
    "unusedpkg",
]

[project.optional-dependencies]
docs = ["toml"]
"""

REQUIREMENTS = """\
# a comment
PyYAML==5.1
requests==2.31.0  # pinned
-r requirements-dev.txt
onlyhere>=1 ; python_version < "3.13"
"""

APP = """\
import os
import yaml
from requests.utils import _private_helper, quote
import requests._internal_utils
from tiny import helpers
from . import sibling
import undeclaredpkg.sub

def lazy():
    import toml
"""


@pytest.fixture
def tiny_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "requirements.txt").write_text(REQUIREMENTS)
    (tmp_path / "requirements-dev.txt").write_text("pytest\n")
    (tmp_path / "README.md").write_text("Needs Python 3.9 or newer.\n")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    pkg = tmp_path / "tiny"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "helpers.py").write_text("")
    (pkg / "app.py").write_text(APP)
    (pkg / "broken.py").write_text("def (:\n")
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "ignored.py").write_text("import shouldnotappear\n")
    return tmp_path


def test_manifests_with_specifiers_and_lines(tiny_repo):
    ev = scan.scan(tiny_repo)
    got = {(m["file"], m["package"]): (m["specifier"], m["line"]) for m in ev["manifests"]}
    assert got[("pyproject.toml", "PyYAML")] == (">=6", 5)
    assert got[("pyproject.toml", "unusedpkg")] == ("", 7)
    assert got[("pyproject.toml", "toml")] == ("", 11)
    assert got[("requirements.txt", "PyYAML")] == ("==5.1", 2)
    assert got[("requirements.txt", "requests")] == ("==2.31.0", 3)
    assert got[("requirements.txt", "onlyhere")] == (">=1", 5)
    assert got[("requirements-dev.txt", "pytest")] == ("", 1)
    assert len(ev["manifests"]) == 8  # comment, -r line and marker text are not packages
    assert ev["repo"]["python_requires"] == ">=3.10"
    assert ev["repo"]["python_mentioned"] == ["3.9", "3.12"]


def test_drift(tiny_repo):
    drift = {d["package"]: d["details"] for d in scan.scan(tiny_repo)["drift"]}
    assert drift["PyYAML"] == {"pyproject.toml": ">=6", "requirements.txt": "==5.1"}
    assert drift["onlyhere"] == {"requirements.txt": ">=1", "pyproject.toml": None}
    assert drift["unusedpkg"] == {"pyproject.toml": "", "requirements.txt": None}
    assert "requests" not in drift
    assert "pytest" not in drift  # dev-only packages are not drift
    assert drift["python"] == {"pyproject.toml": ">=3.10", "README.md": "3.9", "Dockerfile": "3.12"}


def test_usage_undeclared_unused_private(tiny_repo):
    ev = scan.scan(tiny_repo)
    usage = {(u["import"], u["line"]): u for u in ev["usage"]}
    assert usage[("yaml", 2)]["package"] == "PyYAML"
    assert usage[("yaml", 2)]["file"] == "tiny/app.py"
    assert usage[("requests.utils", 3)]["names"] == ["_private_helper", "quote"]
    assert usage[("toml", 10)]["package"] == "toml"  # import inside a function
    imports = {u["import"] for u in ev["usage"]}
    assert not imports & {"os", "tiny", "shouldnotappear"}  # stdlib, local, skipped dirs

    assert [(u["import"], u["line"]) for u in ev["undeclared"]] == [("undeclaredpkg.sub", 7)]
    assert [u["package"] for u in ev["unused"]] == ["unusedpkg", "onlyhere"]  # pytest is a tool
    assert [(p["module"], p["name"]) for p in ev["private_api"]] == [
        ("requests.utils", "_private_helper"), ("requests._internal_utils", "_internal_utils")]


def test_candidates_from_builtin_table(tmp_path):
    (tmp_path / "requirements.txt").write_text("Jinja2==2.11.3\nappdirs\nnose==1.3.7\npydantic>=2\n")
    (tmp_path / "m.py").write_text(
        "import pkg_resources\nfrom pydantic import BaseModel\n"
        "class A(BaseModel):\n    class Config:\n        extra = 'forbid'\n")
    cands = scan.scan(tmp_path)["candidates"]
    assert [c["package"] for c in cands] == ["appdirs", "setuptools", "Jinja2", "pydantic", "nose"]
    assert [c["id"] for c in cands] == ["c1", "c2", "c3", "c4", "c5"]
    assert all(c["provenance"] == "hint" for c in cands)
    assert "m.py" in cands[3]["hint"]


def test_jinja3_is_not_a_candidate(tmp_path):
    (tmp_path / "requirements.txt").write_text("Jinja2>=3.1\n")
    assert scan.scan(tmp_path)["candidates"] == []


@pytest.mark.parametrize("version,spec,ok", [
    ("3.12", ">=3.10", True), ("3.9", ">=3.10", False), ("3.12", ">=3.8,<3.12", False),
    ("3.11", "~=3.10", True), ("3.12", "==3.12.*", True), ("3.13", "!=3.13.*", False),
])
def test_satisfies(version, spec, ok):
    assert scan.satisfies(version, spec) is ok


def test_matches_contract_shape(tiny_repo):
    ev = scan.scan(tiny_repo)
    assert set(ev) == set(FIXTURE) - {"_fixture"}
    assert set(ev["repo"]) == set(FIXTURE["repo"])
    for key in ("manifests", "drift", "usage", "undeclared", "unused", "private_api", "candidates"):
        assert ev[key], key
        for item in ev[key]:
            assert set(item) == set(FIXTURE[key][0]), key
            assert item["provenance"] == ("hint" if key == "candidates" else "observed")


def test_cli_writes_json(tiny_repo, tmp_path, capsys):
    out = tmp_path / "evidence.json"
    assert main(["scan", str(tiny_repo), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == 1
    assert "wrote" in capsys.readouterr().out
    assert main(["scan", str(tmp_path / "missing")]) == 2


TARGET_FILES = ["linkwatch", "tests", "pyproject.toml", "requirements.txt", "requirements-dev.txt",
                "README.md", "Dockerfile"]


@pytest.mark.skipif(not (REPO_ROOT / "linkwatch").is_dir(), reason="LinkWatch target not present")
def test_linkwatch_copy(tmp_path):
    for name in TARGET_FILES:
        src = REPO_ROOT / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, tmp_path / name)
    ev = scan.scan(tmp_path)

    drift = {d["package"]: d["details"] for d in ev["drift"]}
    assert drift["pydantic"] == {"pyproject.toml": ">=2", "requirements.txt": "==1.10.13"}
    assert {u["import"] for u in ev["undeclared"]} == {"certifi"}
    assert "six" in {u["package"] for u in ev["unused"]}
    assert {"module": "requests.utils", "name": "_parse_content_type_header", "file": "linkwatch/encoding.py",
            "line": 5, "provenance": "observed"} in ev["private_api"]
    assert {"package": "setuptools", "import": "pkg_resources", "file": "linkwatch/resources.py", "line": 1,
            "names": ["pkg_resources"], "provenance": "observed"} in ev["usage"]


def test_git_commit_read_from_files(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text("# pack-refs\nf52f393abcdef refs/heads/main\n")
    assert scan.git_commit(tmp_path) == "f52f393"
    (git / "refs" / "heads" / "main").write_text("73f350d0000\n")
    assert scan.git_commit(tmp_path) == "73f350d"
