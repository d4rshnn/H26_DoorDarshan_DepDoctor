# DepDoctor — 4-Minute Live Demonstration Script & Talk Track

> **Track 2:** Maintenance Agent Skill  
> **Team:** DoorDarshan (Hacktoberfest Hack Day Mumbai x ISTE KJSSE)  
> **Target Project:** LinkWatch (open-source uptime checker by rishrek)  
> **Local Model:** Gemma 4 E4B via Ollama (`http://localhost:11434`)

---

## "Before You Go on Stage" Checklist

- [ ] **Ollama Server:** Running locally with model `gemma4:e4b` (`ollama list` confirms `gemma4:e4b` is present).
- [ ] **Context Window:** Environment variable `OLLAMA_CONTEXT_LENGTH=8192` set in your shell before launching.
- [ ] **Target Repository:** Clean clone of LinkWatch located outside the tool repository at `D:\Hackathons\LinkWatch_target` (contains `linkwatch/` and `requirements.txt`).
- [ ] **Python Runtime:** Python 3.12 verified via `py -3.12 --version` (or Python 3.13 via `py -3.13 --version`).
- [ ] **Power & Display:** Laptop plugged into AC mains power; battery set to Best Performance; display scaling and terminal font size set for audience visibility.
- [ ] **Network & Cache:** No internet connection is needed for AI inference (all local); warm the pip wheel cache in advance by running `.\demo\run_demo.ps1` once so sandbox installs take under 10 seconds.
- [ ] **Fallback Video:** Offline screen recording of the full run queued up in a media player, ready to switch to instantly if live laptop execution stalls.

---

## 4-Minute Live Demo Talk Track

| Time | What to do | What to say | What the audience should see |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:45**<br>*(45 sec)* | **1. Demonstrate Target Ground Truth**<br><br>Open terminal at repo root. Show the target repo files and execute the baseline test command:<br>`py -3.12 -m tool.baseline D:\Hackathons\LinkWatch_target --out .sandbox\b.json --workdir .sandbox\b` | "Judges, this is LinkWatch, a real open-source CLI uptime monitor. Without touching any of its source files, DepDoctor executes LinkWatch's test suite inside a pristine, throwaway virtual environment.<br><br>On Python 3.12, exactly 9 tests pass and 1 fails. The failure is an `ImportError` where legacy Jinja2 2.11 attempts to import `soft_unicode` from MarkupSafe—a function removed in modern releases. This is real dependency drift that breaks production code." | Terminal running tests in fresh venv.<br><br>Output shows:<br>`9 passed, 1 failed in ~8.8s`<br>`FAILED tests/test_report.py::test_report_renders_all_sites - ImportError: cannot import name 'soft_unicode' from 'markupsafe'`<br>*(expected, from a hand-verified earlier run)* |
| **0:45 – 1:40**<br>*(55 sec)* | **2. Launch Hermes Agent with DepDoctor Skill**<br><br>Trigger Hermes with the skill and query file:<br>`hermes chat -s depdoctor --query-file demo\ask.txt --oneshot -Q -t terminal,file --in .` | "Now we instruct Hermes Agent using our custom DepDoctor skill. DepDoctor scans runtime manifests, AST-parses real import usage, identifies drift, and feeds verified evidence to our local Gemma 4 E4B model running on Ollama.<br><br>Notice: zero cloud API keys, zero paid services, and zero data leakage. On this laptop, the local model analyzes the evidence and formulates candidate patches in about 45 seconds." | Hermes agent starting up, displaying the tool prompt from `demo\ask.txt`.<br><br>Progress indicators showing evidence collection, candidate proposal generation, and sandboxed test validation. |
| **1:40 – 2:30**<br>*(50 sec)* | **3. Explore the Verification Capsule**<br><br>Open `capsule.html` in the browser or show the live terminal output from `capsule.md`. | "DepDoctor compiles its findings into this cryptographic Capsule report. Notice our visual provenance labels: blue badges show observed static facts, purple badges indicate AI suggestions, and green badges mark empirical sandbox verifications.<br><br>Every claim in this report is strictly grounded in reality—we never hallucinate results or assume a patch works without running tests." | Browser displays `capsule.html`.<br><br>Audience sees clean, color-coded badges:<br>`[observed]`, `[hint]`, `[proposed]`, and `[executed]`.<br><br>Clear tabular summary of dependencies and candidate fixes. |
| **2:30 – 3:15**<br>*(45 sec)* | **4 & 5. Verified Fix vs. The Honest Limits Moment**<br><br>Highlight Candidate 1 (Jinja2) and Candidate 2 (Pydantic) in the capsule table. | "Here is Candidate 1: bumping Jinja2 to `>=3.1,<4`. In our isolated sandbox, the test suite went from 9/10 to a perfect 10/10 passed *(expected, from a hand-verified earlier run)*.<br><br>Now look at our honest limits moment: Candidate 2. The local model also proposed bumping `pydantic>=2`. But when DepDoctor verified it in a clean sandbox, tests failed with 0/10 passed *(expected, from a hand-verified earlier run)* because the codebase uses Pydantic v1 APIs. DepDoctor caught that regression automatically, rejected the bad fix, and kept the repository safe." | Capsule comparison table:<br>• **Jinja2 `>=3.1,<4`**: 10 passed, 0 failed — `[VERIFIED SUCCESS]` *(expected, from hand-verified run)*<br>• **Pydantic `>=2`**: 0 passed, 10 failed — `[REJECTED REGRESSION]` *(expected, from hand-verified run)* |
| **3:15 – 3:45**<br>*(30 sec)* | **6. Reuse & Originality**<br><br>Point out the SHA-256 target immutability hash in the report and the decoupled pipeline design. | "DepDoctor treats the user's codebase as read-only: our pre-and-post SHA-256 fingerprint guarantees the target repository remained completely untouched throughout the entire process.<br><br>Our core innovation is turning non-deterministic local LLMs into reliable maintenance partners by surrounding them with deterministic evidence scanners and sandboxed verification gates." | Immutability check displayed:<br>`Target repository unchanged: YES`<br>`SHA-256 Before == SHA-256 After`<br><br>Architecture showing clean separation of `scan -> baseline -> propose -> validate -> verify -> capsule`. |
| **3:45 – 4:00**<br>*(15 sec)* | **7. Summary & Fallback**<br><br>Show `demo\run_demo.ps1` command in terminal. Conclude presentation. | "If anyone wants to inspect the entire workflow in one step, our PowerShell script `demo\run_demo.ps1` runs the full preflight, fingerprint, and verification pipeline automatically. Thank you, and we welcome your questions!" | Terminal shows clean completion or fallback video ready.<br><br>Team opens floor for judges' Q&A. |

---

## Fallback Contingency Plan

If any unexpected obstacle occurs during the live demonstration (e.g., local model inference timeout, laptop battery throttling, or hardware glitch):

1. **Immediate Fallback:** Switch display to the pre-recorded video demonstrating the complete live run.
2. **Headless Execution:** Run the automated demonstration script in a separate PowerShell window:
   ```powershell
   .\demo\run_demo.ps1 -Target "D:\Hackathons\LinkWatch_target"
   ```
   This script runs all preflight checks, fingerprints the target before and after, runs analyze and finish stages, previews the first 40 lines of `capsule.md`, and launches `capsule.html` automatically.
