---
name: depdoctor
description: Diagnose dependency and maintenance problems in a Python repository and test AI-proposed fixes in a sandbox. Use when asked to analyse, audit or repair the dependencies of a Python repo that has a pytest suite. Produces a Dependency Exit Capsule that separates observed facts, executed test results and AI suggestions.
license: MIT
compatibility: Requires Python 3.12 (py launcher on Windows), Git, and a local OpenAI-compatible model endpoint. No network access or API keys needed by the tool itself.
metadata:
  project: DepDoctor
  author: H26 DoorDarshan
---

# DepDoctor

You analyse a Python repository's dependencies. A set of commands does the real work (reading files, running
tests, applying fixes in a throwaway copy). Your job is to run them in order, write the fix proposals, and
report the results honestly.

## Rules
- Never edit the target repository. All fixes are tested on a sandbox copy by the tool.
- Never invent results. Quote numbers from the files the tool writes, not from memory.
- Always run the tool with `py -3.12` (not `python`): the target does not support Python 3.14.
- If a command fails, show the error text and stop. Do not retry more than once.
- Run commands from the DepDoctor repository folder. TARGET below is the path the user gave you.
- The terminal swallows backslashes. Always write paths with forward slashes, for example
  C:/Users/name/LinkWatch_target, never C:\Users\name\LinkWatch_target.

## Steps

1. Analyse (reads files, runs the target's tests in a clean virtual environment):
   `py -3.12 -m tool analyze TARGET --out-dir out`
   It prints the failing tests and the list of valid evidence references.

2. Read `out/evidence.json` and `out/baseline.json`.

3. Write `out/proposals.json` with at most 2 proposals that fix the failing tests first. Use exactly this shape:
   ```json
   {"proposals": [{
     "id": "p1",
     "problem": "one sentence naming the failing test or problem",
     "kind": "version_change",
     "evidence_refs": ["manifest:requirements.txt:4"],
     "changes": [{"file": "requirements.txt", "action": "replace_line",
                  "old": "<the exact existing line>", "new": "<the new line>"}],
     "rationale": "one or two sentences",
     "risk": "one sentence"
   }]}
   ```
   - `kind` is one of: version_change, api_migration, remove_unused.
   - `action` is one of: replace_line, replace_text, delete_line, create_file.
   - `old` must match one line (or one text occurrence) of the file exactly.
   - Every `evidence_refs` entry must come from the list printed in step 1.
   - Never change files under `tests/`.
   If you cannot write valid JSON, skip this step. The next command falls back to the local model.

4. Test the fixes and build the report. This takes 2 to 3 minutes: set the terminal tool's timeout to 600
   seconds for this command, or add `--no-canary` to make it faster (about 95 seconds):
   `py -3.12 -m tool finish TARGET --out-dir out`
   It validates your proposals, applies each in a sandbox copy, re-runs the tests, and writes
   `out/capsule.md`, `out/capsule.json` and `out/capsule.html`.

5. Read `out/capsule.md` and tell the user:
   - the test counts before and after each proposal, and each verdict (improved, regressed, unchanged);
   - which findings are `[observed]` or `[executed]` (verified) and which are `[ai_generated]` or `[hint]`
     (suggestions, not verified);
   - the limitations listed in the last section.
   Mention the path of `out/capsule.html`.
