import os
from pathlib import Path

import pytest

from tool import baseline


def test_parse_pytest_output_linkwatch():
    text = (
        "FAILED tests/test_report.py::test_report_renders_all_sites - "
        "ImportError: cannot import name 'soft_unicode' from 'markupsafe'\n"
        "1 failed, 9 passed, 1 warning in 8.85s\n"
    )
    result = baseline.parse_pytest_output(text)
    assert result["passed"] == 9
    assert result["failed"] == 1
    assert result["errors"] == 0
    assert result["failures"] == [
        {
            "test": "tests/test_report.py::test_report_renders_all_sites",
            "first_error": "ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
        }
    ]


def test_parse_pytest_output_all_passed():
    text = "10 passed in 8.9s\n"
    result = baseline.parse_pytest_output(text)
    assert result["passed"] == 10
    assert result["failed"] == 0
    assert result["errors"] == 0
    assert result["failures"] == []


def test_parse_pytest_output_errors_and_skips():
    text = (
        "ERROR tests/test_x.py - RuntimeError: setup failed\n"
        "2 errors, 1 skipped, 3 passed in 1.0s\n"
    )
    result = baseline.parse_pytest_output(text)
    assert result["passed"] == 3
    assert result["failed"] == 0
    assert result["errors"] == 2
    assert result["failures"] == [
        {
            "test": "tests/test_x.py",
            "first_error": "RuntimeError: setup failed",
        }
    ]


def test_parse_pytest_output_no_tests_ran():
    text = "no tests ran in 0.01s\n"
    result = baseline.parse_pytest_output(text)
    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["errors"] == 0
    assert result["failures"] == []


def test_parse_pytest_output_failed_without_dash():
    text = (
        "FAILED tests/test_demo.py::test_fail\n"
        "1 failed in 0.05s\n"
    )
    result = baseline.parse_pytest_output(text)
    assert result["passed"] == 0
    assert result["failed"] == 1
    assert result["errors"] == 0
    assert result["failures"] == [
        {
            "test": "tests/test_demo.py::test_fail",
            "first_error": "",
        }
    ]


def test_copy_step_ignores_and_preserves_source(tmp_path):
    src = tmp_path / "source_repo"
    src.mkdir()
    (src / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (src / "tests").mkdir()
    (src / "tests" / "test_app.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (src / ".venv").mkdir()
    (src / ".venv" / "pyvenv.cfg").write_text("home = ...\n", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "app.cpython-312.pyc").write_text("bytecode\n", encoding="utf-8")
    (src / ".pytest_cache").mkdir()
    (src / "foo.egg-info").mkdir()
    (src / "node_modules").mkdir()

    target = tmp_path / "target"
    baseline.copy_target(src, target)

    # Verify target received expected files
    assert (target / "app.py").is_file()
    assert (target / "tests" / "test_app.py").is_file()

    # Verify ignored directories are not in target
    assert not (target / ".git").exists()
    assert not (target / ".venv").exists()
    assert not (target / "__pycache__").exists()
    assert not (target / ".pytest_cache").exists()
    assert not (target / "foo.egg-info").exists()
    assert not (target / "node_modules").exists()

    # Verify source was not altered
    assert (src / "app.py").is_file()
    assert (src / ".git" / "HEAD").is_file()
    assert (src / ".venv" / "pyvenv.cfg").is_file()
    assert (src / "__pycache__" / "app.cpython-312.pyc").is_file()


def test_run_missing_python_returns_shape_and_does_not_raise(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_sample.py").write_text("def test_pass(): pass\n", encoding="utf-8")
    workdir = tmp_path / "work"

    res = baseline.run(repo, python="9.9", workdir=workdir)
    assert res["schema"] == 1
    assert res["python"] is None
    assert res["install"]["exit_code"] is None
    assert "not found" in res["install"]["output_tail"].lower()
    assert res["pytest"]["exit_code"] is None
    assert res["pytest"]["passed"] is None
    assert res["pytest"]["failed"] is None
    assert res["pytest"]["errors"] is None
    assert res["pytest"]["failures"] == []
    assert "skipped" in res["pytest"]
    assert res["pip_check"] is None
    assert res["provenance"] == "executed"


@pytest.mark.skipif(
    os.environ.get("DEPDOCTOR_SLOW") != "1",
    reason="slow integration test; set DEPDOCTOR_SLOW=1 to run",
)
def test_slow_integration(tmp_path):
    repo = tmp_path / "tiny_repo"
    repo.mkdir()
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_tiny.py").write_text(
        "def test_pass():\n"
        "    assert 1 == 1\n\n"
        "def test_fail():\n"
        "    assert 1 == 2\n",
        encoding="utf-8",
    )
    workdir = tmp_path / "work"
    res = baseline.run(repo, python="3.12", workdir=workdir)
    assert res["schema"] == 1
    assert res["provenance"] == "executed"
    assert res["pytest"]["exit_code"] == 1
    assert res["pytest"]["passed"] == 1
    assert res["pytest"]["failed"] == 1
    assert res["pytest"]["errors"] == 0
    assert len(res["pytest"]["failures"]) == 1
    assert "test_fail" in res["pytest"]["failures"][0]["test"]

