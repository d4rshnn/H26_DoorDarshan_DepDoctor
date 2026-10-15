"""Stage 2: run target test suite in a clean virtual environment and record baseline."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA = 1
PROVENANCE = "executed"

DEFAULT_TIMEOUTS = {
    "venv": 180,
    "install": 900,
    "pytest": 300,
    "pip_check": 60,
}

IGNORE_PATTERNS = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".sandbox",
    "*.egg-info",
    "node_modules",
)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_ESCAPE.sub("", text)


def _rmtree(path: Path) -> None:
    """Safely remove a directory tree, clearing read-only flags if needed."""
    if not path.exists():
        return

    def _handle_remove_readonly(func, p, *args):
        import stat

        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_handle_remove_readonly)
    else:
        shutil.rmtree(path, onerror=_handle_remove_readonly)


def copy_target(repo_path: Path, target_dir: Path, workdir: Path | None = None) -> None:
    """Copy repo_path into target_dir ignoring cache and virtual environments."""
    patterns = set(IGNORE_PATTERNS)
    if workdir:
        try:
            rel = workdir.resolve().relative_to(repo_path.resolve())
            if rel.parts:
                patterns.add(rel.parts[0])
        except ValueError:
            pass
    ignore = shutil.ignore_patterns(*patterns)
    shutil.copytree(repo_path, target_dir, ignore=ignore)


def parse_pytest_output(text: str) -> dict:
    """Parse pytest output text into counts (passed, failed, errors) and failures."""
    passed = 0
    failed = 0
    errors = 0
    failures = []

    lines = text.splitlines()
    for raw_line in lines:
        line = strip_ansi(raw_line).strip()
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            rest = line.split(" ", 1)[1].strip()
            if " - " in rest:
                test_id, err = rest.split(" - ", 1)
                first_error = err.strip()[:300]
            else:
                test_id = rest.strip()
                first_error = ""
            failures.append({"test": test_id, "first_error": first_error})

    for raw_line in reversed(lines):
        line = strip_ansi(raw_line).strip()
        if re.search(r"\b(?:no tests ran|\d+\s+(?:passed|failed|errors?|skipped))\b", line):
            m_pass = re.search(r"\b(\d+)\s+passed\b", line)
            if m_pass:
                passed = int(m_pass.group(1))
            m_fail = re.search(r"\b(\d+)\s+failed\b", line)
            if m_fail:
                failed = int(m_fail.group(1))
            m_err = re.search(r"\b(\d+)\s+errors?\b", line)
            if m_err:
                errors = int(m_err.group(1))
            break

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "failures": failures,
    }


def _sub_env() -> dict:
    """Return standard environment dict for subprocesses."""
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COLUMNS"] = "1000"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return env


def run(repo_path, python="3.12", workdir=None, timeouts=None) -> dict:
    """Run the target repo's test suite in a clean venv and return baseline dict."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"not a directory: {repo_path}")

    touts = dict(DEFAULT_TIMEOUTS)
    if timeouts:
        touts.update(timeouts)

    if workdir is None:
        work_path = Path(tempfile.mkdtemp(prefix="depdoctor_baseline_"))
    else:
        work_path = Path(workdir).resolve()
        work_path.mkdir(parents=True, exist_ok=True)

    # 1. Copy repo to workdir/"target"
    target_dir = work_path / "target"
    if target_dir.exists():
        try:
            if target_dir.resolve().is_relative_to(work_path.resolve()):
                _rmtree(target_dir)
        except (ValueError, OSError):
            pass
    copy_target(repo, target_dir, work_path)

    env = _sub_env()

    # 2. Create venv
    venv_dir = work_path / "venv"
    if venv_dir.exists():
        try:
            if venv_dir.resolve().is_relative_to(work_path.resolve()):
                _rmtree(venv_dir)
        except (ValueError, OSError):
            pass

    if sys.platform == "win32":
        venv_cmd = ["py", f"-{python}", "-m", "venv", str(venv_dir)]
    else:
        venv_cmd = [f"python{python}", "-m", "venv", str(venv_dir)]

    try:
        proc_venv = subprocess.run(
            venv_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=touts["venv"],
            env=env,
        )
        if proc_venv.returncode != 0:
            tail = (proc_venv.stdout or "").strip()
            msg = f"Python {python} not found: {tail}" if tail else f"Python {python} not found"
            return {
                "schema": SCHEMA,
                "python": None,
                "install": {"exit_code": None, "output_tail": msg},
                "pytest": {
                    "exit_code": None,
                    "passed": None,
                    "failed": None,
                    "errors": None,
                    "failures": [],
                    "skipped": f"Python {python} not found",
                },
                "pip_check": None,
                "provenance": PROVENANCE,
            }
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        return {
            "schema": SCHEMA,
            "python": None,
            "install": {"exit_code": None, "output_tail": f"Python {python} not found: {exc}"},
            "pytest": {
                "exit_code": None,
                "passed": None,
                "failed": None,
                "errors": None,
                "failures": [],
                "skipped": f"Python {python} not found",
            },
            "pip_check": None,
            "provenance": PROVENANCE,
        }

    # 3. Find venv's python binary & record version
    if sys.platform == "win32":
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"

    if not venv_python.is_file():
        return {
            "schema": SCHEMA,
            "python": None,
            "install": {"exit_code": None, "output_tail": f"Python binary not found at {venv_python}"},
            "pytest": {
                "exit_code": None,
                "passed": None,
                "failed": None,
                "errors": None,
                "failures": [],
                "skipped": "venv python executable missing",
            },
            "pip_check": None,
            "provenance": PROVENANCE,
        }

    env["VIRTUAL_ENV"] = str(venv_dir)

    try:
        proc_ver = subprocess.run(
            [str(venv_python), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=env,
        )
        raw_ver = (proc_ver.stdout or "").strip()
        python_version = raw_ver.split()[-1] if raw_ver else python
    except (subprocess.SubprocessError, OSError):
        python_version = python

    # 4. Install dependencies
    req = target_dir / "requirements.txt"
    req_dev = target_dir / "requirements-dev.txt"
    pyproj = target_dir / "pyproject.toml"

    install_output = ""
    install_exit_code = 0
    install_cmd = None

    if req.is_file():
        install_cmd = [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"]
        if req_dev.is_file():
            install_cmd.extend(["-r", "requirements-dev.txt"])
    elif pyproj.is_file():
        install_cmd = [str(venv_python), "-m", "pip", "install", "."]
    else:
        install_output = "no dependency files found\n"

    if install_cmd:
        try:
            proc_inst = subprocess.run(
                install_cmd,
                cwd=target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=touts["install"],
                env=env,
            )
            install_output = proc_inst.stdout or ""
            install_exit_code = proc_inst.returncode
        except subprocess.TimeoutExpired:
            install_output = f"pip install timed out after {touts['install']}s"
            install_exit_code = None
        except (subprocess.SubprocessError, OSError) as exc:
            install_output = f"pip install failed: {exc}"
            install_exit_code = 1

    # Check if pytest is available
    pytest_available = False
    try:
        proc_pytest_check = subprocess.run(
            [str(venv_python), "-m", "pytest", "--version"],
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=env,
        )
        if proc_pytest_check.returncode == 0:
            pytest_available = True
    except (subprocess.SubprocessError, OSError):
        pass

    # If install succeeded (or no dependency files) and pytest is not available, install pytest
    if not pytest_available and install_exit_code == 0:
        try:
            proc_pytest_inst = subprocess.run(
                [str(venv_python), "-m", "pip", "install", "pytest"],
                cwd=target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=touts["install"],
                env=env,
            )
            if install_output:
                install_output += "\n" + (proc_pytest_inst.stdout or "")
            else:
                install_output = proc_pytest_inst.stdout or ""
            if proc_pytest_inst.returncode == 0:
                pytest_available = True
            else:
                if install_exit_code == 0:
                    install_exit_code = proc_pytest_inst.returncode
        except subprocess.TimeoutExpired:
            install_output += f"\npip install pytest timed out after {touts['install']}s"
        except (subprocess.SubprocessError, OSError):
            pass

    output_tail = install_output[-1500:]

    # 5. Run pytest
    pytest_data = None
    if not pytest_available:
        pytest_data = {
            "exit_code": None,
            "passed": None,
            "failed": None,
            "errors": None,
            "failures": [],
            "skipped": "pytest is not available in venv",
        }
    else:
        pytest_cmd = [
            str(venv_python),
            "-m",
            "pytest",
            "-q",
            "-rfE",
            "--tb=line",
            "-p",
            "no:cacheprovider",
        ]
        try:
            proc_pytest = subprocess.run(
                pytest_cmd,
                cwd=target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=touts["pytest"],
                env=env,
            )
            parsed = parse_pytest_output(proc_pytest.stdout or "")
            pytest_data = {
                "exit_code": proc_pytest.returncode,
                "passed": parsed["passed"],
                "failed": parsed["failed"],
                "errors": parsed["errors"],
                "failures": parsed["failures"],
            }
        except subprocess.TimeoutExpired:
            pytest_data = {
                "exit_code": None,
                "passed": None,
                "failed": None,
                "errors": None,
                "failures": [
                    {
                        "test": "pytest",
                        "first_error": f"timed out after {touts['pytest']}s",
                    }
                ],
            }
        except (subprocess.SubprocessError, OSError) as exc:
            pytest_data = {
                "exit_code": None,
                "passed": None,
                "failed": None,
                "errors": None,
                "failures": [],
                "skipped": f"failed to run pytest: {exc}",
            }

    # 7. Run pip check
    pip_check_str = None
    try:
        proc_pip_check = subprocess.run(
            [str(venv_python), "-m", "pip", "check"],
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=touts["pip_check"],
            env=env,
        )
        pip_check_str = (proc_pip_check.stdout or "").strip()
    except subprocess.TimeoutExpired:
        pip_check_str = f"timed out after {touts['pip_check']}s"
    except (subprocess.SubprocessError, OSError) as exc:
        pip_check_str = f"pip check failed: {exc}"

    return {
        "schema": SCHEMA,
        "python": python_version,
        "install": {"exit_code": install_exit_code, "output_tail": output_tail},
        "pytest": pytest_data,
        "pip_check": pip_check_str,
        "provenance": PROVENANCE,
    }


def main():
    parser = argparse.ArgumentParser(description="DepDoctor baseline test runner")
    parser.add_argument("repo_path", nargs="?", default=".", help="Path to target repository")
    parser.add_argument("--python", default="3.12", help="Python version to use (default: 3.12)")
    parser.add_argument("--workdir", default=None, help="Working directory for copy and venv")
    parser.add_argument("--out", default="baseline.json", help="Output JSON file (default: baseline.json)")
    args = parser.parse_args()

    result = run(args.repo_path, python=args.python, workdir=args.workdir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    pytest_res = result.get("pytest", {})
    if "skipped" in pytest_res:
        status = f"pytest skipped ({pytest_res['skipped']})"
    else:
        status = (
            f"passed={pytest_res.get('passed')} "
            f"failed={pytest_res.get('failed')} "
            f"errors={pytest_res.get('errors')}"
        )
    print(f"baseline: python={result.get('python')} {status} -> wrote {args.out}")


if __name__ == "__main__":
    main()
