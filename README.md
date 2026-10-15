# DepDoctor

DepDoctor helps maintainers find dependency problems in Python projects and check proposed fixes against the project's own tests. Its main report is the **Dependency Exit Capsule**, which separates facts read from files, results produced by commands, model suggestions, knowledge-table hints, and anything that remains unverified.

## Problem and solution overview

Old or mismatched dependencies can break software, and changing versions safely is difficult: a change that looks reasonable can introduce new failures. DepDoctor scans dependency declarations and imports, records a test baseline, asks a local AI model for possible fixes, validates those proposals, then applies accepted changes to separate sandbox copies and runs the tests again. The target repository itself is not patched by the verification step.

The LinkWatch run described here is an example from test fixtures. Its recorded Python 3.12 baseline is 9 passed and 1 failed. The failure is `tests/test_report.py::test_report_renders_all_sites`, caused by Jinja2 2.11.3 importing `soft_unicode` from newer MarkupSafe. A proposal to use `Jinja2>=3.1,<4` was tested in a sandbox copy and the recorded result is 10 passed. The validator and tests also catch wrong proposals: for example, a pydantic v2 change conflicts with LinkWatch's pydantic v1 code and fails verification.

Each capsule item is labelled:

| Label | Meaning |
|---|---|
| `observed` | Read from repository files without running the target. |
| `executed` | Produced by a command, such as installation or tests. |
| `ai_generated` | Written by the model; a suggestion, not verified by its label. |
| `hint` | A lead from the built-in knowledge table; not verified. |
| `unverified` | Could not be checked, or required input is missing. |

## Setup instructions

These steps describe a local Windows setup. The target supports Python 3.12 and 3.13; Python 3.14 is unsupported by the target.

1. Install Git and Python 3.12, including the Windows `py` launcher.
2. Install [Ollama](https://ollama.com), then download the local model:

   ```powershell
   ollama pull gemma4:e4b
   ```

   If model replies are truncated, optionally set `OLLAMA_CONTEXT_LENGTH=8192` in the shell before starting Ollama. A larger context window can help avoid truncated replies.
3. Install Hermes Agent from [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com). Configure its custom local endpoint as `http://localhost:11434/v1` and select `gemma4:e4b`. Use no cloud provider and do not sign in to Nous Portal. See Hermes documentation for the correct skills folder location; copy `skill/depdoctor/` there.
4. Clone LinkWatch separately so the demo scans a clean target copy:

   ```powershell
   git clone https://github.com/rishrek/LinkWatch.git
   ```

DepDoctor uses a local Ollama endpoint. **No API keys are needed anywhere.**

## Usage instructions

Run commands from the DepDoctor repository root. Replace `<LinkWatch-path>` with the path to the separate LinkWatch clone. The target repository is never modified.

```powershell
py -3.12 -m tool analyze <LinkWatch-path> --out-dir out
py -3.12 -m tool finish <LinkWatch-path> --out-dir out
```

`analyze` scans the repository and runs its tests in a clean environment (the baseline). `finish` uses `out/proposals.json` if it holds valid proposals (written by an agent such as Hermes); otherwise it asks the local model directly. It then validates the proposals, verifies each in a sandbox copy, and writes `capsule.json`, `capsule.md`, `capsule.html` and `run.json` into the output folder. Open `out/capsule.html` in a browser to read the report.

The individual stages are:

```powershell
py -3.12 -m tool scan <LinkWatch-path> --out evidence.json
py -3.12 -m tool.baseline <LinkWatch-path> --out baseline.json --workdir .sandbox/baseline
py -3.12 -m tool propose evidence.json --out proposals.json --repo <LinkWatch-path> --baseline baseline.json
py -3.12 -m tool check-proposals proposals.json --evidence evidence.json --repo <LinkWatch-path>
py -3.12 -m tool verify <LinkWatch-path> proposals.json baseline.json --out verify.json
py -3.12 -m tool capsule --evidence evidence.json --baseline baseline.json --proposals proposals.json --verify verify.json --out-dir out
py -3.12 -m tool report out/capsule.json --out out/capsule.html
```

Read a capsule by checking the provenance label on every item: `observed` means read from files, `executed` means command output, `ai_generated` means a model suggestion, `hint` means a built-in lead, and `unverified` means unchecked or missing information.

To ask Hermes to run the skill (after copying `skill/depdoctor/` into the Hermes skills folder):

```powershell
hermes chat -s depdoctor --query-file ask.txt --oneshot -Q -t terminal,file --in <this repo>
```

`ask.txt` holds a one-line request naming the target path. <FILL IN: result of the Hermes run once tested>

Run DepDoctor's tests with:

```powershell
py -3.12 -m pytest tests_tool -q
```

## Architecture

```text
scan (read files)
  -> evidence.json
baseline (run target tests in a clean environment)
  -> baseline.json
propose (local Gemma model)
  -> proposals.json
validate (deterministic safety and evidence checks)
verify (apply accepted proposals to sandbox copies and re-run tests)
  -> verify.json
capsule (merge labelled evidence and results)
  -> capsule.json + capsule.md + capsule.html
```

| Step | Implementation | Uses AI? |
|---|---|---|
| Scan | Reads dependency files and imports; no target execution. | No |
| Baseline | Installs target dependencies and runs its tests in a throwaway environment. | No |
| Propose | Sends evidence to the local model and receives candidate changes. | **Yes; this is the only AI step.** |
| Validate | Checks proposal shape, evidence references, and safe paths/changes. | No |
| Verify | Applies each accepted change to a sandbox copy and compares test results. | No |
| Capsule | Formats inputs into provenance-labelled JSON and Markdown. | No |

## Dependencies

DepDoctor itself uses only the Python standard library. External tools are Ollama, Hermes Agent, and pytest. Pytest and the target's own packages are installed only in throwaway virtual environments used for the target; they are not installed into the DepDoctor repository.

## AI model details

| Model name | Model version | Model source | Access method | Applicable model license or usage terms | Role of the model in the project |
|---|---|---|---|---|---|
| Gemma 4 E4B | Tag `gemma4:e4b`; Ollama version: `<FILL IN: output of ollama --version>` (confirm) | Google DeepMind, distributed through [Ollama](https://ollama.com/library/gemma4) (confirm) | Local Ollama server on `localhost`, OpenAI-compatible chat completions, no API key, no cloud | Apache 2.0 as stated in the event handbook; confirm against the [Ollama model page](https://ollama.com/library/gemma4) (confirm) | Proposes fixes as JSON; code validates them; the tests decide. |

| Model name | Model version | Model source | Access method | Applicable model license or usage terms | Role of the model in the project |
|---|---|---|---|---|---|
| Hermes Agent | v0.22.1 (2026.9.24) (confirm) | Nous Research, open source (confirm) | Installed locally; uses the local Ollama endpoint | MIT according to its repository (confirm) | Agent runtime that runs the DepDoctor skill's commands; the model behind it is Gemma 4 E4B. |

## Known limitations

- Only the target's own tests are evidence; their coverage of changed code is unknown.
- The recorded verification is on Python 3.12. Python 3.13: `<FILL IN: Python 3.13 result>`.
- A small local model can propose wrong fixes. Validation and test runs help detect them but cannot prove a change is safe in every use.
- The scanner skips unparseable Python files.
- Security vulnerabilities are out of scope.
- A model call takes about 45 seconds on a laptop without a GPU (reported project experience; this is not a timing guarantee).
- Hermes multi-step reliability with a small model is limited. `finish` falls back to a direct local-model call when no valid `proposals.json` exists.
- The tool installs packages with pip inside throwaway environments, so that step needs network access.
- Windows is the tested platform.

## Original contribution

Our contribution is the evidence-labelled propose, validate, and sandbox-verify loop, the Dependency Exit Capsule, the HTML report, and the DepDoctor skill. Hermes Agent is the runtime; we did not modify Hermes.

## How to reuse on another repository

Use a Python repository with `requirements.txt` or `pyproject.toml` and a pytest suite. Clone or copy it separately, then substitute its path for `<LinkWatch-path>` in the commands above. Review the capsule's provenance labels and rerun any suggested checks that matter for your project.

## AI assistance disclosure

Claude and Codex coding assistants were used. Every change was reviewed and tested by a human team member before it was committed.

## Third-party acknowledgements

LinkWatch by rishrek (MIT), Hermes Agent by Nous Research, Ollama, Gemma 4 by Google DeepMind, and pytest.

## Pre-existing work

No pre-existing DepDoctor work is claimed here; the target repository is LinkWatch by rishrek. The original target README is preserved in [TARGET_README.md](TARGET_README.md). The DepDoctor additions are identified in the licence notice below.

## Team

H26 DoorDarshan: Darshan Mahale, Aarya Burande, and `<FILL IN full name>`.
