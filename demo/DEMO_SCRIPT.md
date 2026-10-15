# DepDoctor — 4-Minute Live Demonstration Script & Talk Track

> **Track 2:** Maintenance Agent Skill  
> **Team:** DoorDarshan (Hacktoberfest Hack Day Mumbai x ISTE KJSSE)  
> **Target Project:** LinkWatch (open-source uptime checker by rishrek, cloned outside this repo)  
> **Local Model:** Gemma 4 E4B via Ollama (`http://localhost:11434`)

---

## "Before You Go on Stage" Checklist

- [ ] **Ollama Server:** Running locally with model `gemma4:e4b` (`ollama list` confirms `gemma4:e4b` is present).
- [ ] **Context Window:** Environment variable `OLLAMA_CONTEXT_LENGTH=8192` set before launching.
- [ ] **Target Repository:** Clean clone of LinkWatch located outside the tool repository at `D:\Hackathons\LinkWatch_target` (contains `linkwatch/` and `requirements.txt`).
- [ ] **Python Runtime:** Python 3.12 verified via `py -3.12 --version`.
- [ ] **Power & Display:** Laptop plugged into AC mains power; battery set to Best Performance; display scaling and font size set for audience visibility.
- [ ] **Warm Cache:** Run `demo\run_demo.ps1` once in advance to warm the pip wheel cache.
- [ ] **Fallback Video:** Offline screen recording of the full run queued up in a media player, ready to play if live laptop execution stalls.

---

## 4-Minute Live Demo Talk Track

| Time | What to do | What to say | What the audience should see |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:45**<br>*(45 sec)* | **1. Demonstrate Target Ground Truth**<br><br>Open terminal at repo root. Show the target repo files and execute the baseline test command:<br>`py -3.12 -m tool.baseline <target> --out .sandbox\b.json --workdir .sandbox\b`<br>*(or run the demo script's analyze step)* | "Judges, this is LinkWatch, a real open-source CLI uptime monitor. Without touching any of its files, DepDoctor runs its test suite inside a clean, isolated virtual environment.<br><br>On Python 3.12, 9 tests pass and 1 fails. The failure is an `ImportError` in `test_report_renders_all_sites` where legacy Jinja2 2.11 attempts to import `soft_unicode` from MarkupSafe—a function removed in modern MarkupSafe releases. This is real dependency drift that breaks production code." | Terminal running tests in a fresh venv.<br><br>Output shows:<br>`tests: 9 passed, 1 failed, 0 errors`<br>`FAILING tests/test_report.py::test_report_renders_all_sites: ImportError: cannot import name 'soft_unicode' from 'markupsafe'`<br>*(expected, from a hand-verified earlier run)* |
| **0:45 – 1:45**<br>*(60 sec)* | **2. Propose Fixes with Local Model**<br><br>**Primary command (to be confirmed at rehearsal):**<br>```powershell<br>hermes chat -s depdoctor --query-file demo\ask.txt --oneshot -Q -t terminal,file --in <this repo><br>```<br><br>**Direct fallback command:**<br>```powershell<br>py -3.12 -m tool finish <target> --out-dir out<br>```<br><br>*(NOT YET MEASURED: Hermes behaviour with the skill; decide at rehearsal. If Hermes does not complete the run, run `py -3.12 -m tool finish <target> --out-dir out` directly and say so openly).* | "Now we ask our local model, Gemma 4 E4B via Ollama, to propose fixes. Zero cloud API keys, zero paid services, and zero network calls.<br><br>The finish stage takes about 95 seconds total: the local model call takes about 57 seconds to formulate and validate candidate changes, followed by about 38 seconds of sandbox verification.<br><br>While it runs, notice what the tool is doing: it feeds verified static evidence and baseline failures to the model, validates the model's output syntax and evidence references, and prepares clean sandbox directories."<br><br>*(Presenter tip: Narrate what the tool is doing during the wait, or switch to the pre-recorded video if time is constrained).* | Terminal executing `finish` stage (or Hermes agent).<br><br>Output streams model invocation, validation of proposals against evidence, and launch of sandbox verification. |
| **1:45 – 2:30**<br>*(45 sec)* | **3. Explore the Verification Capsule**<br><br>Open `capsule.html` in the default browser (or preview `capsule.md` in the terminal). | "DepDoctor compiles its output into a Dependency Exit Capsule across markdown, JSON, and this standalone HTML report.<br><br>The capsule contains ten sections, and every single line is prefixed with an honest provenance label. In `capsule.html`, badge colours distinguish claims clearly:<br>• **green** for `observed` facts read directly from files<br>• **blue** for `executed` commands that actually ran in a sandbox<br>• **purple** for `ai_generated` model proposals<br>• **grey** for built-in `hint` rules<br>• **amber** for `unverified` open questions.<br><br>Nothing is assumed or taken on trust." | Browser displaying `capsule.html`.<br><br>Audience sees the ten capsule sections with colour-coded badges (`observed` green, `executed` blue, `ai_generated` purple, `hint` grey, `unverified` amber). |
| **2:30 – 3:15**<br>*(45 sec)* | **4 & 5. Verified Fix vs. The Honest Limits Moment**<br><br>Scroll to the proposals and verification section of `capsule.html`. | **Verified Fix:**<br>"Proposal p1 replaced `Jinja2==2.11.3` with `Jinja2>=3.1,<4` in `requirements.txt`. DepDoctor applied it to a throwaway copy and re-ran the tests: 10 passed, 0 failed (before: 9 passed, 1 failed). The verdict is **improved**: 1 test fixed, and none newly broken."<br><br>**Honest Limits Moment (choose live branch):**<br>• **Branch A (if the live run produced a rejected or regressed proposal):**<br>"Look at Proposal p2 on screen: it was evaluated and marked [read verdict and reason from screen]. The sandbox tests caught the problem before anything could break main."<br>• **Branch B (if only proposal p1 was produced):**<br>"The model proposed one fix this time and it was verified. In earlier runs it sometimes proposed a wrong change; the validator and the sandbox tests exist to catch those, and the capsule's last section lists what remains unverified." | Capsule verification table showing:<br>`p1: improved (before 9 passed, 1 failed, 0 errors; after 10 passed, 0 failed, 0 errors)`<br><br>And either a rejected proposal with its reason (Branch A), or Section 10 listing remaining unverified items (Branch B). |
| **3:15 – 3:45**<br>*(30 sec)* | **6. Target Immutability & Reuse**<br><br>Point to the console output of `demo/run_demo.ps1`. | "DepDoctor treats the user's repository as read-only. Our automation script `demo/run_demo.ps1` prints the target's SHA-256 fingerprint before and after running. Because every modification is tested only inside isolated sandbox folders, the target repository remains completely unchanged.<br><br>The tool is modular and reusable on any Python repository." | Script console output showing:<br>`Target SHA-256 (before): ...`<br>`Target SHA-256 (after):  ...`<br>`Target repository unchanged: YES` |
| **3:45 – 4:00**<br>*(15 sec)* | **7. Summary & Fallback**<br><br>Conclude presentation and open floor for questions. | "To run the complete pipeline reproducibly in one command, `demo\run_demo.ps1` handles preflight checks, per-step timing, and report generation automatically. If live inference stalls at any point, our full run recording is available as a fallback. Thank you, and we welcome your questions!" | Audience sees the final demo script summary and elapsed time. Presentation concludes. |

---

## Fallback Contingency Plan

If any unexpected obstacle occurs during the live demonstration (e.g. local model inference timeout, laptop battery throttling, or hardware glitch):

1. **Immediate Fallback:** Switch display to the pre-recorded video demonstrating the complete live run.
2. **One-Command Headless Execution:** Run the automated demonstration script in a separate PowerShell window:
   ```powershell
   powershell -File demo\run_demo.ps1 -Target "D:\Hackathons\LinkWatch_target"
   ```
   This script runs all preflight checks, fingerprints the target before and after, runs analyze (~38 s) and finish (~95 s), previews `capsule.md`, and opens `capsule.html` automatically.

---

## Rehearsal Log

| Run | Date/time | Command | Total seconds | Verdicts | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| | | | | | |
| | | | | | |
| | | | | | |
