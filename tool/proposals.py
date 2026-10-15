"""Stage 4: ask the local model for fix proposals and validate them.

`validate` is deterministic: it decides whether an AI proposal is well formed and can be applied
to the repo. Each proposal is applied to an in-memory copy of the files it touches, so a later
change in the same proposal is checked against the result of the earlier ones.

Change rules (shared with verify):
  replace_line  "old" equals exactly one line of the file, ignoring surrounding whitespace;
                "new" replaces it (the old indentation is kept when "new" has none).
  delete_line   "old" as for replace_line; that line is removed.
  replace_text  "old" occurs exactly once in the file; "new" replaces it.
  create_file   the file must not exist yet; "new" is its full content.

`propose` talks only to a model on localhost through its OpenAI-style chat-completions endpoint.
"""

import json
import re
import urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlparse

MAX_PROPOSALS = 3
KINDS = {"version_change", "api_migration", "remove_unused"}
ACTIONS = {"replace_line": {"old", "new"}, "replace_text": {"old", "new"},
           "delete_line": {"old"}, "create_file": {"new"}}
PROPOSAL_KEYS = {"id", "problem", "kind", "evidence_refs", "changes", "rationale", "risk"}
CHANGE_KEYS = {"file", "action", "old", "new"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_BASE_URL = "http://localhost:11434/v1"


# ---------------------------------------------------------------- validation

def evidence_refs(evidence):
    """Every ref a proposal may cite, e.g. 'manifest:requirements.txt:4' or 'candidate:c1'."""
    refs = set()
    for key, prefix in (("manifests", "manifest"), ("usage", "usage"), ("undeclared", "undeclared"),
                        ("private_api", "private_api")):
        refs.update(f"{prefix}:{i['file']}:{i['line']}" for i in evidence.get(key, []))
    refs.update(f"drift:{i['package']}" for i in evidence.get("drift", []))
    refs.update(f"unused:{i['package']}" for i in evidence.get("unused", []))
    refs.update(f"candidate:{i['id']}" for i in evidence.get("candidates", []))
    return refs


def check_path(root, file):
    """Return (relative posix path, None) if `file` is a safe path inside root, else (None, reason)."""
    if not isinstance(file, str) or not file.strip():
        return None, "file must be a non-empty string"
    if file.startswith(("/", "\\")) or PureWindowsPath(file).drive or PurePosixPath(file).is_absolute():
        return None, f"absolute path not allowed: {file}"
    parts = PurePosixPath(file.replace("\\", "/")).parts
    if ".." in parts:
        return None, f"path leaves the repo: {file}"
    if parts and parts[0] == ".git":
        return None, f"editing .git is not allowed: {file}"
    rel = "/".join(p for p in parts if p != ".")
    try:
        (root / rel).resolve().relative_to(root.resolve())
    except ValueError:
        return None, f"path leaves the repo: {file}"
    name = parts[-1]
    if parts[0] == "tests" or name.startswith("test_") or name == "conftest.py":
        return None, f"proposals may not edit tests, the tests decide if a fix works: {file}"
    return rel, None


def matching_lines(lines, old):
    """Indexes of lines equal to `old`, ignoring surrounding whitespace."""
    target = old.strip()
    return [i for i, line in enumerate(lines) if line.strip() == target]


def apply_change(text, change):
    """Apply one change to file text (None if the file does not exist). Return (new_text, error)."""
    action, old, new = change["action"], change.get("old"), change.get("new")
    if action == "create_file":
        return (new, None) if text is None else (None, "create_file on a file that already exists")
    if text is None:
        return None, "file does not exist in the repo"
    if action == "replace_text":
        count = text.count(old)
        if count != 1:
            return None, f"'old' text found {count} times, expected exactly once"
        return text.replace(old, new, 1), None

    if "\n" in old.strip():
        return None, f"{action} 'old' must be a single line"
    lines = text.splitlines(keepends=True)
    hits = matching_lines(lines, old)
    if len(hits) != 1:
        return None, f"'old' line {old.strip()!r} found {len(hits)} times, expected exactly once"
    i = hits[0]
    if action == "delete_line":
        del lines[i]
    else:
        ending = lines[i][len(lines[i].rstrip("\r\n")):] or "\n"
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        body = new if new[:1].isspace() else indent + new
        lines[i] = body.rstrip("\r\n") + ending
    return "".join(lines), None


def _check_change(change, root, files):
    """Validate one change and apply it to `files` (rel path -> text or None). Return reason or None."""
    if not isinstance(change, dict):
        return "change must be an object"
    unknown = set(change) - CHANGE_KEYS
    if unknown:
        return f"unknown change fields: {sorted(unknown)}"
    action = change.get("action")
    if action not in ACTIONS:
        return f"unknown action {action!r}, allowed: {sorted(ACTIONS)}"
    for field in ACTIONS[action]:
        if not isinstance(change.get(field), str):
            return f"{action} needs a string '{field}'"
    if action != "create_file" and not change["old"].strip():
        return f"{action} 'old' must not be empty"
    rel, reason = check_path(root, change.get("file"))
    if reason:
        return reason
    if rel not in files:
        path = root / rel
        files[rel] = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
    if files[rel] is None and action != "create_file":
        return f"file does not exist in the repo: {change['file']}"
    files[rel], error = apply_change(files[rel], change)
    return f"{rel}: {error}" if error else None


def check_proposal(proposal, root, known_refs):
    """Return the reasons a proposal is invalid (empty list when it is accepted)."""
    if not isinstance(proposal, dict):
        return ["proposal must be an object"]
    reasons = []
    missing, unknown = PROPOSAL_KEYS - set(proposal), set(proposal) - PROPOSAL_KEYS
    if missing:
        reasons.append(f"missing fields: {sorted(missing)}")
    if unknown:
        reasons.append(f"unknown fields: {sorted(unknown)}")
    for field in ("id", "problem", "rationale", "risk"):
        if field in proposal and not (isinstance(proposal[field], str) and proposal[field].strip()):
            reasons.append(f"'{field}' must be a non-empty string")
    if "kind" in proposal and proposal["kind"] not in KINDS:
        reasons.append(f"unknown kind {proposal['kind']!r}, allowed: {sorted(KINDS)}")

    refs = proposal.get("evidence_refs")
    if not (isinstance(refs, list) and refs and all(isinstance(r, str) and r.strip() for r in refs)):
        reasons.append("evidence_refs must be a non-empty list of strings")
    elif known_refs is not None:
        bad = [r for r in refs if r not in known_refs]
        if bad:
            reasons.append(f"evidence_refs not found in evidence: {bad}")

    changes = proposal.get("changes")
    if not (isinstance(changes, list) and changes):
        reasons.append("changes must be a non-empty list")
    else:
        files = {}
        for n, change in enumerate(changes, 1):
            reason = _check_change(change, root, files)
            if reason:
                reasons.append(f"change {n}: {reason}")
                break
    return reasons


def validate(proposals, evidence, repo_path):
    """Split proposals into (accepted, rejected). `proposals` is a dict or JSON text.

    Each rejected item is {"id", "reason", "proposal"}. `evidence` may be None to skip the
    check that evidence_refs point at real evidence.
    """
    if isinstance(proposals, str):
        try:
            proposals = json.loads(proposals)
        except json.JSONDecodeError as exc:
            return [], [{"id": None, "reason": f"not valid JSON: {exc}", "proposal": None}]
    if not isinstance(proposals, dict) or not isinstance(proposals.get("proposals"), list):
        return [], [{"id": None, "reason": 'expected an object {"proposals": [...]}', "proposal": proposals}]

    root = Path(repo_path)
    known_refs = evidence_refs(evidence) if evidence is not None else None
    accepted, rejected, seen = [], [], set()
    for n, proposal in enumerate(proposals["proposals"]):
        pid = proposal.get("id") if isinstance(proposal, dict) else None
        reasons = check_proposal(proposal, root, known_refs)
        if n >= MAX_PROPOSALS:
            reasons.insert(0, f"more than {MAX_PROPOSALS} proposals")
        if repr(pid) in seen:
            reasons.append(f"duplicate id {pid!r}")
        seen.add(repr(pid))
        if reasons:
            rejected.append({"id": pid, "reason": "; ".join(reasons), "proposal": proposal})
        else:
            accepted.append(proposal)
    return accepted, rejected


# ---------------------------------------------------------------- model call

def check_local(base_url):
    host = urlparse(base_url).hostname
    if urlparse(base_url).scheme not in ("http", "https") or host not in LOCAL_HOSTS:
        raise ValueError(f"refusing to send evidence to a non-local model endpoint: {base_url}")


def http_post_json(url, payload, timeout=600):
    """POST JSON with urllib, ignoring any proxy settings so the request stays on this machine."""
    check_local(url)
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _merge_repeated_proposals(pairs):
    """JSON object hook: small models sometimes repeat the "proposals" key; keep every list."""
    obj = {}
    for key, value in pairs:
        if key == "proposals" and isinstance(obj.get(key), list) and isinstance(value, list):
            obj[key] = obj[key] + value
        else:
            obj[key] = value
    return obj


def parse_reply(text):
    """Pull the JSON object out of a model reply (plain, fenced ```json block, or surrounded by prose)."""
    text = (text or "").strip()
    candidates = [text]
    candidates += re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if "{" in text:
        candidates.append(text[text.index("{"):text.rindex("}") + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate, object_pairs_hook=_merge_repeated_proposals)
        except json.JSONDecodeError:
            continue
    raise ValueError("reply is not valid JSON")


SHAPE = {"proposals": [{
    "id": "p1", "problem": "short text", "kind": "version_change",
    "evidence_refs": ["manifest:requirements.txt:4"],
    "changes": [{"file": "requirements.txt", "action": "replace_line", "old": "Jinja2==2.11.3",
                 "new": "Jinja2>=3.1,<4"}],
    "rationale": "why this fixes it", "risk": "what could break"}]}

SYSTEM_PROMPT = f"""You fix dependency problems in a Python repository.
You get evidence collected by deterministic tools and the text of the relevant files.
Propose at most {MAX_PROPOSALS} small, independent fixes. Reply with JSON only, in exactly this shape:
{json.dumps(SHAPE)}
Rules:
- kind is one of: version_change, api_migration, remove_unused.
- evidence_refs: one or more refs copied from the [brackets] in the EVIDENCE list, without the brackets,
  for example "manifest:requirements.txt:4".
- action is one of: replace_line, replace_text, delete_line, create_file.
- replace_line: "old" is one whole line copied exactly from a file shown below; "new" replaces it.
- replace_text: "old" is text that appears exactly once in the file; "new" replaces it.
- delete_line: "old" is the whole line to delete.
- create_file: "new" is the full content of a new file.
- "file" is a path relative to the repo root. Never edit tests.
- HINT lines are suggestions from a lookup table, not facts. Prefer the smallest change that works."""

MAX_FILE_CHARS = 3000
MAX_TOTAL_CHARS = 9000


def build_prompt(evidence, repo_path):
    """Compact user message: the refs for flagged problems plus the files a fix would touch."""
    flagged = {d["package"].lower() for d in evidence.get("drift", [])}
    flagged |= {u["package"].lower() for u in evidence.get("unused", [])}
    flagged |= {c["package"].lower() for c in evidence.get("candidates", [])}
    private_files = {p["file"] for p in evidence.get("private_api", [])}
    undeclared_files = {u["file"] for u in evidence.get("undeclared", [])}

    lines = ["EVIDENCE ([ref] fact)"]
    files = []
    for m in evidence.get("manifests", []):
        if m["package"].lower() in flagged:
            lines.append(f"[manifest:{m['file']}:{m['line']}] {m['package']}{m['specifier']}")
            files.append(m["file"])
    for d in evidence.get("drift", []):
        lines.append(f"[drift:{d['package']}] " + ", ".join(f"{f} {s!r}" for f, s in d["details"].items()))
    for u in evidence.get("usage", []):
        if u["package"].lower() in flagged or u["file"] in private_files:
            lines.append(f"[usage:{u['file']}:{u['line']}] {u['import']} -> {', '.join(u['names'])}")
            files.append(u["file"])
    for u in evidence.get("undeclared", []):
        lines.append(f"[undeclared:{u['file']}:{u['line']}] imports {u['import']}, not declared")
    for u in evidence.get("unused", []):
        lines.append(f"[unused:{u['package']}] declared, never imported")
    for p in evidence.get("private_api", []):
        lines.append(f"[private_api:{p['file']}:{p['line']}] {p['module']}.{p['name']} is private")
    for c in evidence.get("candidates", []):
        lines.append(f"[candidate:{c['id']}] HINT {c['package']}: {c['hint']}")
    files += sorted(undeclared_files | private_files)

    lines.append("\nFILES")
    root, total = Path(repo_path), 0
    for rel in dict.fromkeys(files):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
        if total + len(text) > MAX_TOTAL_CHARS:
            lines.append(f"--- {rel} (omitted, prompt size limit)")
            continue
        total += len(text)
        lines.append(f"--- {rel}\n{text.rstrip()}")
    return "\n".join(lines)


def propose(evidence_path, out_path, model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL,
            repo_path=None, http=http_post_json):
    """Ask the local model for proposals, validate them, retry once, write out_path. Return the result."""
    check_local(base_url)
    evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    repo_path = repo_path or evidence["repo"]["path"]
    url = base_url.rstrip("/") + "/chat/completions"
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(evidence, repo_path)}]
    result = {"schema": 1, "model": model, "provenance": "ai_generated", "attempts": 0,
              "proposals": [], "rejected": []}

    # Thinking is switched off: on Ollama's default 4096-token context the reasoning alone can use up
    # the window and leave an empty reply. The retry re-sends only the original prompt plus the
    # rejection reasons (not the previous reply) for the same reason.
    for attempt in (1, 2):
        result["attempts"] = attempt
        try:
            response = http(url, {"model": model, "messages": messages, "temperature": 0, "stream": False,
                                  "response_format": {"type": "json_object"}, "reasoning_effort": "none"})
            choice = response["choices"][0]
            reply = choice["message"].get("content") or ""
        except Exception as exc:  # network down, model missing, unexpected response shape
            result["error"] = f"could not get a reply from {model} at {base_url}: {exc}"
            break
        try:
            accepted, rejected = validate(parse_reply(reply), evidence, repo_path)
        except ValueError as exc:
            reason = str(exc)
            if choice.get("finish_reason") == "length":
                reason += " (reply cut off: the model ran out of context; raise OLLAMA_CONTEXT_LENGTH)"
            accepted, rejected = [], [{"id": None, "reason": reason, "proposal": None, "raw": reply[:2000]}]
        for item in rejected:
            item["attempt"] = attempt
        result["rejected"] += rejected
        if accepted:
            result["proposals"] = accepted
            result.pop("error", None)
            break
        reasons = "; ".join(f"{r['id'] or 'reply'}: {r['reason']}" for r in rejected) or "no proposals given"
        result["error"] = f"no usable proposal after {attempt} attempt(s): {reasons}"
        messages = messages[:2] + [{"role": "user", "content": f"Your previous answer was rejected: {reasons}\n"
                                                               "Reply again with corrected JSON only, same shape."}]

    Path(out_path).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
