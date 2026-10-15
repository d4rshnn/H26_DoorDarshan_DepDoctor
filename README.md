# DepDoctor

**Don't trust an AI's dependency fix. Make it prove itself.**

DepDoctor reads a Python repository, runs its own tests, asks a **local open-weight model** for fixes, applies each fix to a **throwaway copy** of the repo, re-runs the tests, and writes a **Dependency Exit Capsule**: a report where every line says how we know it (read from files, produced by a command, or only suggested by the AI).

- **Runs entirely on your machine.** No cloud, no paid API, no API keys.
- **The target repo is never modified.** Fixes are tested on copies.
- **It checks its own checker.** A deliberate known-bad change is run through the verifier, and the capsule reports whether the verifier caught it.
- **It tells you how much to trust a green result.** A static check shows whether any test actually reaches the changed package.

Built for Hacktoberfest Hack Day Mumbai x ISTE KJSSE, Track 2 (Maintenance Agent Skill). Target repository: [LinkWatch](https://github.com/rishrek/LinkWatch) by rishrek.

## Quick start (Windows, about 10 minutes plus the model download)

You need: Git, Python 3.12 (with the `py` launcher), and [Ollama](https://ollama.com). Python 3.14 is not supported by the target.

```powershell
# 1. Get DepDoctor
git clone https://github.com/d4rshnn/H26_DoorDarshan_DepDoctor.git
cd H26_DoorDarshan_DepDoctor

# 2. Get a clean copy of the target project (kept outside this folder)
git clone https://github.com/rishrek/LinkWatch.git ..\LinkWatch_target

# 3. Download the local model (about 6 GB, once). Ollama must be installed and running.
ollama pull gemma4:e4b

# 4. Analyse the target, test AI-proposed fixes, build the report (about 2.5 minutes)
py -3.12 -m tool finish ..\LinkWatch_target --out-dir out

# 5. Open the report
start out\capsule.html
```

DepDoctor itself needs no `pip install`: it uses only the Python standard library. The target's own packages are installed automatically, but only inside throwaway virtual environments under `out\.sandbox`.

What you should see at the end of step 4 (measured on our laptop, see [Measured results](#measured-results)):

```text
p1: improved (before 9 passed, 1 failed, 0 errors; after 10 passed, 0 failed, 0 errors)
strength p1: Jinja2: 2 test file(s), e.g. tests/test_report.py -> linkwatch/cli.py -> linkwatch/report.py -> jinja2
canary: removed click, verdict regressed, detected yes
```

The model's proposal can differ from run to run. The tests, not the model, decide whether a fix is accepted.

## Problem and solution overview

Old or mismatched dependencies break software, and changing versions safely is hard: a change that looks reasonable can introduce new failures. AI can suggest fixes, but a suggestion is not evidence.

DepDoctor connects AI-assisted analysis with real repository inspection and execution:

1. **Scan** dependency files and imports (no execution).
2. **Baseline:** run the target's own tests in a clean environment, before any change.
3. **Propose:** a local model suggests fixes as structured JSON.
4. **Validate:** code rejects malformed, unsafe or unevidenced proposals (the proposal must cite real evidence, may not edit tests, may not touch paths outside the repo).
5. **Verify:** apply each accepted proposal to a separate copy, re-run the tests, compare with the baseline.
6. **Report:** merge everything into the capsule (`capsule.md`, `capsule.json`, `capsule.html`).

On LinkWatch (Python 3.12) the baseline is **9 passed, 1 failed**: `tests/test_report.py::test_report_renders_all_sites` fails with `ImportError: cannot import name 'soft_unicode' from 'markupsafe'`, because the pinned Jinja2 2.11.3 meets MarkupSafe 3. The model proposed `Jinja2>=3.1,<4`; DepDoctor tested it in a sandbox copy and measured **10 passed, 0 failed**.

### How to read a capsule

Every line starts with a label (colour-coded in the HTML report):

| Label | Meaning |
|---|---|
| `observed` | Read from repository files without running the target. |
| `executed` | Produced by a command we actually ran (install, tests, diff). |
| `ai_generated` | Written by the model. A suggestion, not verified by its label. |
| `hint` | A lead from our built-in knowledge table. Not verified. |
| `unverified` | Could not be checked, or an input is missing. |

The capsule has ten sections: problem identified, repository and dependency details, risk indicators, usage locations, AI analysis, proposed changes, suggested tests, executed test results (before and after, evidence strength, verifier self-check), compatibility gaps, and unresolved concerns and limitations.

## Setup instructions

1. **Git** and **Python 3.12** with the Windows `py` launcher. Check with `py -3.12 --version`.
2. **Ollama** from [ollama.com](https://ollama.com), then `ollama pull gemma4:e4b`.
3. Optional but recommended: give Ollama a larger context window so long model replies are not cut off. In PowerShell:

   ```powershell
   [Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH","65536","User")
   ```

   Then quit Ollama completely and start it again (Ollama reads this only at start-up). `ollama ps` shows the context in use.
4. A clean clone of the target (see Quick start, step 2).
5. Optional, only for the Hermes route: see [Using Hermes](#using-hermes-agent).

To run DepDoctor's own tests:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest tests_tool -q
```

## Usage instructions

All commands run from the DepDoctor folder. `<target>` is the path of any Python repo that has a `requirements.txt` or `pyproject.toml` and a pytest suite.

| Goal | Command |
|---|---|
| Everything in one go | `py -3.12 -m tool finish <target> --out-dir out` |
| Only look: scan and baseline | `py -3.12 -m tool analyze <target> --out-dir out` |
| One-command demo with checks | `powershell -File demo\run_demo.ps1 -Target <target>` |
| Skip the verifier self-check | add `--no-canary` to `finish` |

`finish` uses `out/proposals.json` if it already holds valid proposals (for example written by an agent); otherwise it asks the local model directly. Output files in `out\`:

| File | Content |
|---|---|
| `capsule.html` | the report, open in a browser |
| `capsule.md` | the same report as text |
| `capsule.json` | machine-readable report |
| `evidence.json`, `baseline.json`, `verify.json` | raw results of each stage |
| `canary.json`, `strength.json` | verifier self-check and test-reach analysis |
| `run.json` | timings, commands, proposal origin |

Individual stages, if you want to run them separately: `scan`, `propose`, `check-proposals`, `verify`, `capsule`, `report` (see `py -3.12 -m tool --help`), plus `py -3.12 -m tool.baseline <target> --out baseline.json`.

### Try it on your own repository

```powershell
git clone <your repo url> ..\my_repo
py -3.12 -m tool finish ..\my_repo --out-dir out_my_repo
start out_my_repo\capsule.html
```

If the repo has no pytest suite, the capsule says the tests could not run, and every result stays `unverified`.

## Architecture

```text
 repo (read-only)
   |
   +--> scan ---------> evidence.json   [observed]   code only
   +--> baseline -----> baseline.json   [executed]   code only: clean venv, install, pytest
   |
   +--> propose ------> proposals.json  [ai_generated]   local model (the ONLY AI step)
   +--> validate        reject bad / unsafe / unevidenced proposals     code only
   +--> verify -------> verify.json     [executed]   copy repo, apply fix, reinstall, re-run tests
   +--> canary -------> canary.json     [executed]   known-bad change must be caught
   +--> strength -----> strength.json   [observed]   which tests reach the changed package
   |
   +--> capsule ------> capsule.md / .json / .html   every line labelled
```

| Step | Implementation | Uses AI? |
|---|---|---|
| Scan | AST and manifest parsing, drift detection | No |
| Baseline | Fresh venv, install, run the target's tests | No |
| Propose | Sends evidence and failing tests to the local model | **Yes, the only AI step** |
| Validate | Schema, evidence references, safe paths, tests cannot be edited, localhost only | No |
| Verify | Applies each fix to a copy, re-runs the tests, compares with baseline | No |
| Canary | Removes a package the code imports in a copy; the verifier must report a regression | No |
| Strength | Static import graph from tests to the changed package (cannot see dynamic imports) | No |
| Capsule | Merges and labels everything | No |

Code lives in `tool/` (one module per stage), tests in `tests_tool/`, the Hermes skill in `skill/depdoctor/`.

## Dependencies

DepDoctor uses only the Python standard library. External tools: Ollama, Hermes Agent (optional route), and pytest, which is installed only inside the throwaway virtual environments created for the target. The target's own packages are installed only inside those environments. Pip needs network access for that step.

## AI model details

| Model name | Model version | Model source | Access method | Applicable model license or usage terms | Role of the model in the project |
|---|---|---|---|---|---|
| Gemma 4 E4B | Ollama tag `gemma4:e4b` (Q4_K_M, 7.5B parameters as reported by Ollama); run with Ollama 0.40.4 | Google DeepMind, distributed through [Ollama](https://ollama.com/library/gemma4) | Local Ollama server on `localhost:11434`, OpenAI-compatible chat completions, no API key, no cloud | Apache 2.0 as stated in the event handbook; see the [model page](https://ollama.com/library/gemma4) | Proposes fixes as JSON. Code validates them and the tests decide. |
| Hermes Agent | v0.22.1 (2026.9.24) | [Nous Research](https://hermes-agent.nousresearch.com), open source | Installed locally; configured with a custom local endpoint (Ollama) | MIT according to its repository | Agent runtime that can run the DepDoctor skill's commands. The model behind it is Gemma 4 E4B. |

## Using Hermes Agent

The skill in `skill/depdoctor/SKILL.md` follows the [agentskills.io](https://agentskills.io) format. It tells an agent to run `analyze`, write `proposals.json`, then run `finish`, and to report results with their labels.

```powershell
# 1. Install Hermes Agent (Windows PowerShell), see https://hermes-agent.nousresearch.com
iex (irm https://hermes-agent.nousresearch.com/install.ps1)

# 2. Point it at local Ollama: run `hermes model`, choose "Custom endpoint",
#    URL http://localhost:11434/v1, model gemma4:e4b, context 65536.
#    Do NOT choose Nous Portal or any cloud provider, and skip login prompts.

# 3. Install the skill and check that it is listed
Copy-Item -Recurse skill\depdoctor "$env:LOCALAPPDATA\hermes\skills\depdoctor"
hermes skills list

# 4a. Talk to Hermes interactively with the skill loaded, then type a request such as:
#     "Use the depdoctor skill to analyse C:/path/to/LinkWatch_target"  (use forward slashes)
hermes chat -s depdoctor -t terminal,file,skills --in .

# 4b. Or run one request without interaction
hermes chat --query-file demo\ask.txt --oneshot -Q -s depdoctor -t terminal,file,skills --in .
```

**Honest status.** In our tests the skill loads in Hermes, and Hermes ran `analyze` through it correctly. With the 4B local model on a laptop without a GPU, Hermes did not reliably complete the full multi-step run: its own instructions and tool definitions need more than Ollama's default 4,096-token window, and small models lose the task after a tool call. `finish` therefore does not depend on Hermes: it runs the whole pipeline itself and falls back to a direct local-model call when no valid `proposals.json` exists. The verified, reproducible path is the `finish` command above.

## Measured results

Measured on the team's Windows laptop (31 GB RAM, no discrete GPU, Python 3.12.2, Ollama 0.40.4, `gemma4:e4b`), against a clean clone of LinkWatch at commit `f52f393`, on 2026-10-15.

| What | Result |
|---|---|
| Baseline (target's own tests, before any change) | 9 passed, 1 failed (`soft_unicode` ImportError) |
| Model proposal (live, first attempt) | `Jinja2==2.11.3` to `Jinja2>=3.1,<4` in `requirements.txt` |
| Verification in a sandbox copy | 10 passed, 0 failed; 1 test fixed, none newly failing; verdict `improved` |
| Evidence strength | 2 test files reach Jinja2, e.g. `tests/test_report.py -> linkwatch/cli.py -> linkwatch/report.py -> jinja2` |
| Verifier self-check | Removed `click` in a sandbox copy: 0 passed; verdict `regressed`; detected: yes |
| Target repository after the run | Unchanged (checksums of all 54 files identical before and after) |
| Time | `analyze` about 38 s; `finish` about 95 s without the self-check and about 150 to 165 s with it; one model call about 57 s |
| DepDoctor's own tests | 196 passed, 3 slow tests skipped |

## Known limitations

- Only the target's own tests are evidence. "A test reaches the package" means a test imports a path to it, not that the changed lines ran, and dynamic imports are not seen.
- Verified end to end on Python 3.12 only. Python 3.13 has not been verified end to end.
- A small local model can propose wrong fixes, and its proposals vary between runs. Validation and tests reduce that risk but cannot prove a change safe in every use.
- Unparseable Python files are skipped by the scanner and not listed.
- Security vulnerabilities are out of scope.
- About 57 s per model call on a laptop without a GPU; a full run takes minutes.
- Hermes multi-step orchestration with the 4B model was not reliable (see above).
- The pip install step in the sandbox needs network access.
- Windows is the only tested platform (the tool uses the `py` launcher).

## Original contribution

Hermes Agent is an existing agent runtime; we did not modify it. What we built is new:

- the **evidence-labelled propose, validate, sandbox-verify loop**, which keeps AI suggestions strictly separate from observed and executed facts;
- the **verifier self-check** (a known-bad change that must be caught) and the **test-reach evidence-strength** analysis;
- the **Dependency Exit Capsule** in JSON, Markdown and self-contained HTML;
- the **DepDoctor skill** that exposes the workflow to an agent.

## How to reuse on another repository

Use any Python repository with a `requirements.txt` or `pyproject.toml` and a pytest suite. Clone it separately and run `py -3.12 -m tool finish <path> --out-dir out`. The stages are independent modules with documented JSON in and out, so any one can be reused on its own (for example the sandbox verifier with proposals written by another tool).

## AI assistance disclosure

Claude and Codex coding assistants were used to write code. Every change was reviewed and tested by a human team member before it was committed.

## Third-party acknowledgements

[LinkWatch](https://github.com/rishrek/LinkWatch) by rishrek (MIT), [Hermes Agent](https://hermes-agent.nousresearch.com) by Nous Research, [Ollama](https://ollama.com), Gemma 4 by Google DeepMind, [pytest](https://pytest.org).

## Pre-existing work

None claimed. The target repository is LinkWatch by rishrek, and this repository is a fork of it. The original LinkWatch README is kept in [TARGET_README.md](TARGET_README.md). See `NOTICE` for licence notes.

## Team

H26 DoorDarshan: Darshan Mahale, Aarya Burande, Leena ([@cyanol05](https://github.com/cyanol05)).
