import json
from pathlib import Path

import pytest

from tool import strength as S

FILES = {
    "app/__init__.py": "",
    "app/cli.py": "import click\n\nfrom . import util\n\n\ndef report():\n    from .render import render_page\n"
                  "    return render_page()\n",
    "app/render.py": "from jinja2 import Environment\n\nfrom .helpers.fmt import pretty\n",
    "app/helpers/__init__.py": "",
    "app/helpers/fmt.py": "from .. import util\n\n\ndef pretty(x):\n    return x\n",
    "app/util.py": "import os\n",
    "app/store.py": "import yaml\n",
    "app/broken.py": "def (:\n",
    "tests/test_cli.py": "from app.cli import report\n",
    "tests/test_util.py": "import app.util\n",
    "tests/conftest.py": "import pytest\n",
    "test_toplevel.py": "from app import store\n",
    ".venv/lib/x.py": "import app.render\n",
    "scripts/tool.py": "import app.store\n",
}

USAGE = [
    {"package": "click", "import": "click", "file": "app/cli.py", "line": 1, "names": ["click"]},
    {"package": "Jinja2", "import": "jinja2", "file": "app/render.py", "line": 1, "names": ["Environment"]},
    {"package": "PyYAML", "import": "yaml", "file": "app/store.py", "line": 1, "names": ["yaml"]},
    {"package": "pytest", "import": "pytest", "file": "tests/conftest.py", "line": 1, "names": ["pytest"]},
    {"package": "requests", "import": "requests", "file": "tests/test_util.py", "line": 9, "names": []},
]
EVIDENCE = {"usage": USAGE, "manifests": [
    {"file": "requirements.txt", "package": p, "specifier": "", "line": n, "provenance": "observed"}
    for n, p in enumerate(["click", "Jinja2", "PyYAML", "six", "requests"], 1)]}


@pytest.fixture
def repo(tmp_path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_import_graph_resolves_every_form(repo):
    g = S.import_graph(repo)
    assert g["app/cli.py"] == {"app/__init__.py", "app/util.py", "app/render.py"}  # from . import / lazy from .x
    assert g["app/render.py"] == {"app/helpers/__init__.py", "app/helpers/fmt.py", "app/__init__.py"}
    assert g["app/helpers/fmt.py"] == {"app/__init__.py", "app/util.py"}  # from .. import util
    assert g["tests/test_util.py"] == {"app/__init__.py", "app/util.py"}  # import a.b
    assert g["tests/test_cli.py"] == {"app/__init__.py", "app/cli.py"}  # from a.b import name
    assert g["app/util.py"] == set() and g["app/broken.py"] == set()  # stdlib ignored; syntax error tolerated
    assert ".venv/lib/x.py" not in g


def test_lazy_import_chain_reaches_jinja2(repo):
    r = S.reach(repo, EVIDENCE, ["Jinja2"])["Jinja2"]
    assert r["source_files"] == ["app/render.py"]
    assert r["tests_reaching"] == ["tests/test_cli.py"]
    assert r["chain"] == "tests/test_cli.py -> app/cli.py -> app/render.py -> jinja2"
    assert r["strength"] == "tests_reach_change" and r["provenance"] == "observed"


def test_reached_and_unreached_packages(repo):
    r = S.reach(repo, EVIDENCE, ["click"])["click"]
    assert r["tests_reaching"] == ["tests/test_cli.py"] and r["chain"] == "tests/test_cli.py -> app/cli.py -> click"
    yaml = S.reach(repo, EVIDENCE, ["pyyaml"])["pyyaml"]
    assert yaml["tests_reaching"] == ["test_toplevel.py"]  # test_*.py outside tests/ also counts
    six = S.reach(repo, EVIDENCE, ["six"])["six"]
    assert six["strength"] == "no_test_reaches_change" and six["chain"] is None
    assert six["note"] == "no non-test file imports this package"


def test_test_only_usage_is_not_a_source(repo):
    r = S.reach(repo, EVIDENCE, ["requests"])["requests"]
    assert r["source_files"] == [] and r["strength"] == "no_test_reaches_change"


def test_unreached_source_file(repo):
    (repo / "app" / "orphan.py").write_text("import attr\n")
    ev = dict(EVIDENCE, usage=EVIDENCE["usage"] + [
        {"package": "attrs", "import": "attr", "file": "app/orphan.py", "line": 1, "names": []}])
    r = S.reach(repo, ev, ["attrs"])["attrs"]
    assert r["source_files"] == ["app/orphan.py"] and r["tests_reaching"] == [] and "note" not in r


def test_example_chain_prefers_real_tests_over_conftest(repo):
    (repo / "tests" / "conftest.py").write_text("import app.render\n")
    r = S.reach(repo, EVIDENCE, ["Jinja2"])["Jinja2"]
    assert r["tests_reaching"] == ["tests/conftest.py", "tests/test_cli.py"]
    assert r["chain"].startswith("tests/test_cli.py")


def test_never_executes_target_code(repo):
    (repo / "app" / "boom.py").write_text("raise SystemExit('executed!')\n")
    (repo / "tests" / "test_boom.py").write_text("import app.boom\n")
    S.reach(repo, EVIDENCE, ["Jinja2"])  # would exit if the target were imported


def test_src_layout(tmp_path):
    for rel, text in {"src/pkg/__init__.py": "", "src/pkg/core.py": "import toml\n",
                      "tests/test_core.py": "from pkg.core import x\n"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    ev = {"usage": [{"package": "toml", "import": "toml", "file": "src/pkg/core.py", "line": 1, "names": []}]}
    assert S.reach(tmp_path, ev, ["toml"])["toml"]["chain"] == "tests/test_core.py -> src/pkg/core.py -> toml"


@pytest.mark.parametrize("change,expected", [
    ({"file": "requirements.txt", "action": "replace_line", "old": "Jinja2==2.11.3", "new": "Jinja2>=3.1,<4"},
     ["Jinja2"]),
    ({"file": "requirements-dev.txt", "action": "delete_line", "old": "nose==1.3.7"}, ["nose"]),
    ({"file": "pyproject.toml", "action": "replace_line", "old": '    "pydantic>=2",', "new": '    "pydantic<2",'},
     ["pydantic"]),
    ({"file": "pyproject.toml", "action": "replace_line", "old": 'requires-python = ">=3.10"',
      "new": 'requires-python = ">=3.11"'}, []),
    ({"file": "app/render.py", "action": "replace_text", "old": "a", "new": "b"}, ["Jinja2"]),
    ({"file": "app/new.py", "action": "create_file", "new": "x = 1\n"}, []),
])
def test_changed_packages(change, expected):
    ev = dict(EVIDENCE, manifests=EVIDENCE["manifests"] + [{"file": "pyproject.toml", "package": "pydantic"}])
    assert S.changed_packages({"changes": [change]}, ev) == expected


def test_changed_packages_deduplicates():
    changes = [{"file": "requirements.txt", "action": "replace_line", "old": "jinja2==2", "new": "Jinja2>=3"},
               {"file": "app/render.py", "action": "replace_text", "old": "a", "new": "b"}]
    assert S.changed_packages({"changes": changes}, EVIDENCE) == ["jinja2"]


def test_for_proposals(repo):
    proposals = {"proposals": [
        {"id": "p1", "changes": [{"file": "requirements.txt", "action": "replace_line", "old": "Jinja2==2",
                                  "new": "Jinja2>=3"}]},
        {"id": "p2", "changes": [{"file": "requirements.txt", "action": "delete_line", "old": "six"}]},
        {"id": "p3", "changes": [{"file": "app/new.py", "action": "create_file", "new": ""}]}]}
    verify_result = {"results": [{"proposal_id": "p1"}, {"proposal_id": "p2"}, {"proposal_id": "p3"}]}
    out = S.for_proposals(repo, EVIDENCE, proposals, verify_result)
    assert out["provenance"] == "observed"
    by_id = {p["proposal_id"]: p for p in out["proposals"]}
    assert by_id["p1"]["reach"][0]["chain"] == "tests/test_cli.py -> app/cli.py -> app/render.py -> jinja2"
    assert by_id["p2"]["reach"][0]["strength"] == "no_test_reaches_change"
    assert by_id["p3"] == {"proposal_id": "p3", "packages": [], "reach": []}
    json.dumps(out)  # serialisable for strength.json
