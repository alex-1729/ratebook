"""Step 2 of the brochure repaint: write units.json for one page folder.

    .venv/bin/python scripts/repaint/translate.py PAGE_DIR [--backend files|claude] [--minutes 15] [--model NAME]
                                                  [--effort low|medium|high|xhigh|max] [--force] [--no-memory]

From Python:
    translate_page(page_dir, backend, on_log=None, **options) -> dict   validated units (units.json is written)

Backends
- files:  units.json must already be in the folder (written by hand or by another agent); it is checked and returned.
- claude: in this order, stopping at the first that applies:
    1. the folder already holds a valid units.json for this page (same page hash): returned, nothing else done;
    2. a page with the same hash was translated before (translations/_memory/pages.json): its units are reused;
    3. pre-pass: lines settled by rule (translate_rules.py) or found in the translation memory
       (translate_memory.py) are marked "done" in page.json;
    4. if any line is left (or there is outlined text), the `claude` program (Claude Code) is run headless in the
       page folder with the user's existing Claude Code sign-in (no API key). It gets translator_prompt.md as its
       instructions, may only read files in the page folder and write units.json there, and has none of the user's
       own settings, plugins, MCP servers, CLAUDE.md files or memory. If its file does not pass units.py it gets ONE
       follow-up turn in the same conversation quoting the problems; if it still does not pass, TranslateError is
       raised. A refusal for load or usage limits is retried once after 60 s, then RateLimited is raised;
    5. rule, memory and model units are merged into units.json (each unit has "origin"), checked, and the model's
       units are added to the memory.

PAGE_DIR/translate_run.json records how the page was done: line counts by rule / memory / model, seconds, tokens,
the CLI's reported cost, validation problems and follow-ups.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import units as units_mod  # noqa: E402
import translate_memory as memory_mod  # noqa: E402
import translate_rules as rules_mod  # noqa: E402

REPO = HERE.parent.parent
PROMPT_FILE = HERE / "translator_prompt.md"
BACKENDS = ("files", "claude")
DEFAULT_MINUTES = 15.0
DEFAULT_MAX_TURNS = 40
RUN_RECORD = "translate_run.json"
EFFORTS = ("low", "medium", "high", "xhigh", "max")   # claude --effort, from `claude --help`


class TranslateError(RuntimeError):
    """The page could not be translated; the message says why and what to do."""


class ClaudeUnavailable(TranslateError):
    """The claude program is missing or not signed in."""


# --------------------------------------------------------------------------------------------- child processes
_children = set()
_children_lock = threading.Lock()
_stopping = threading.Event()


def stop_all():
    """Stop every claude process this module started (for Ctrl-C / SIGTERM in the caller)."""
    _stopping.set()
    with _children_lock:
        procs = list(_children)
    for p in procs:
        try:
            p.terminate()
        except OSError:
            pass
    deadline = time.time() + 5
    for p in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except OSError:
                pass


def _run_child(args, cwd, env, stdin_text, timeout):
    """Run one child to completion; returns (returncode, stdout, stderr). Stoppable through stop_all()."""
    if _stopping.is_set():
        raise TranslateError("Stopped before Claude Code was started.")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True     # Ctrl-C reaches the runner, which then stops this child itself
    p = subprocess.Popen([str(a) for a in args], cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    with _children_lock:
        _children.add(p)
    try:
        out, err = p.communicate(stdin_text.encode("utf-8") if stdin_text is not None else None, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.terminate()
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
        raise TranslateError("Claude Code did not finish within %.0f minutes and was stopped. "
                             "Try again, or allow more time." % (timeout / 60))
    finally:
        with _children_lock:
            _children.discard(p)
    if _stopping.is_set():
        raise TranslateError("Stopped while Claude Code was running.")
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# --------------------------------------------------------------------------------------------- finding claude
def find_claude():
    """The claude program: $REPAINT_CLAUDE_BIN, then the one bundled with this repo's app (Agent SDK), then PATH."""
    env = os.environ.get("REPAINT_CLAUDE_BIN")
    if env:
        if Path(env).is_file():
            return Path(env)
        raise ClaudeUnavailable("Claude Code is not installed or not signed in: REPAINT_CLAUDE_BIN is set to %s, "
                                "but there is no such file." % env)
    sdk = REPO / "app" / "node_modules" / "@anthropic-ai"
    exe_names = ("claude.exe", "claude") if os.name == "nt" else ("claude",)
    # The SDK ships the program in a per-platform package next to it (claude-agent-sdk-darwin-arm64, -win32-x64, ...);
    # older SDK versions kept it inside claude-agent-sdk itself.
    candidates = [sdk / "claude-agent-sdk"]
    try:
        candidates += sorted(p for p in sdk.iterdir() if p.name.startswith("claude-agent-sdk-"))
    except OSError:
        pass
    for d in candidates:
        for n in exe_names:
            f = d / n
            if f.is_file():
                return f
    on_path = shutil.which("claude")
    if on_path:
        return Path(on_path)
    raise ClaudeUnavailable("Claude Code is not installed or not signed in: no `claude` program was found. Run "
                            "`npm install` in app/ (it brings Claude Code with the Agent SDK), install Claude Code, "
                            "or set REPAINT_CLAUDE_BIN to the program.")


def child_env():
    """The caller's environment without the switches of any Claude Code session the runner itself was started from,
    so the child behaves as Claude Code does when the user starts it, minus CLAUDE.md files and memory."""
    env = dict(os.environ)
    for k in list(env):
        if k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT") or k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "CLAUDE_PREVIEW_")):
            del env[k]
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


_auth_checked = {}


def check_signed_in(binary):
    """Raise ClaudeUnavailable unless `claude auth status` says the user is signed in. Checked once per program."""
    key = str(binary)
    if key in _auth_checked:
        return _auth_checked[key]
    try:
        code, out, err = _run_child([binary, "auth", "status"], Path.cwd(), child_env(), None, 60)
    except OSError as e:
        raise ClaudeUnavailable("Claude Code is not installed or not signed in: %s could not be started (%s)." % (binary, e))
    try:
        status = json.loads(out)
    except ValueError:
        status = None
    if not isinstance(status, dict) or not status.get("loggedIn"):
        text = (out.strip() or err.strip() or "exit code %d" % code)[:500]
        raise ClaudeUnavailable("Claude Code is not installed or not signed in: `claude auth status` says: %s\n"
                                "Sign in by running Claude Code once (`claude`, then /login) and try again." % text)
    _auth_checked[key] = {"authMethod": status.get("authMethod"), "subscriptionType": status.get("subscriptionType")}
    return _auth_checked[key]


# --------------------------------------------------------------------------------------------- claude backend
def claude_args(binary, page_dir, prompt_text, session, resume, model=None, max_turns=DEFAULT_MAX_TURNS, effort=None):
    """The command line for one headless turn. See the module docstring for the reasons."""
    args = [binary, "-p",
            "--output-format", "json",
            "--system-prompt", prompt_text,                  # the translator prompt replaces Claude Code's default
                                                             # prompt, so no machine/git details are sent
            "--tools", "Read,Write,Edit",                    # built-in tools: read files, write units.json
            "--allowedTools", "Read", "Write(./units.json)", "Edit(./units.json)",
            "--permission-mode", "dontAsk",                  # anything not allowed above is refused, never asked
            "--restricted",                                  # file tools confined to the page folder; no Bash/web
            "--setting-sources", "",                         # none of the user's settings, plugins, hooks
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',   # no MCP servers
            "--disable-slash-commands",                      # no skills or commands
            "--max-turns", str(int(max_turns))]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    args += ["--resume", session] if resume else ["--session-id", session]
    return args


def _first_message(page_dir, n_done):
    d = Path(page_dir).resolve()
    extra = (" %d of its lines are already translated (marked \"done\" in page.json): leave those out." % n_done) if n_done else ""
    return ("Translate this page. Its files are in the folder %s:\n"
            "- %s (the page's text)\n"
            "- %s (the page as it looks)\n"
            "Follow your instructions and write the result with the Write tool to %s.%s "
            "Do not create any other file. When the file is written, reply with the single word: done."
            % (d, d / "page.json", d / "page.png", d / "units.json", extra))


def _fix_message(page_dir, problems):
    d = Path(page_dir).resolve()
    return ("The program checked %s and it does not pass. Problems:\n%s\n\n"
            "Read the file again, fix every problem listed (re-read page.json or page.png if you need to), and write "
            "the corrected file to the same place. Do not change anything else. Reply with the single word: done."
            % (d / "units.json", "\n".join("- " + p for p in problems)))


_AUTH_HINTS = ("not logged in", "please run /login", "invalid api key", "oauth token", "authentication_error",
               "credit balance", "login required", "/login")
_RATE_HINTS = ("rate limit", "rate_limit", "ratelimit", "429", "529", "overloaded", "usage limit", "limit reached",
               "too many requests", "try again later")


class RateLimited(TranslateError):
    """The subscription or the service refused for load or usage limits."""


def _one_turn(binary, page_dir, prompt_text, message, session, resume, opts, timeout, log):
    args = claude_args(binary, page_dir, prompt_text, session, resume, opts.get("model"), opts["max_turns"], opts.get("effort"))
    started = time.time()
    code, out, err = _run_child(args, page_dir, child_env(), message, timeout)
    seconds = round(time.time() - started, 1)
    result = None
    for chunk in (out.strip(), out.strip().splitlines()[-1] if out.strip() else ""):
        try:
            result = json.loads(chunk)
            break
        except ValueError:
            continue
    if isinstance(result, list):   # some versions print the whole message list; the result is the last item
        result = next((m for m in reversed(result) if isinstance(m, dict) and m.get("type") == "result"), None)
    text = ((result or {}).get("result") or "") if isinstance(result, dict) else ""
    blob = (text + "\n" + err + "\n" + (out if result is None else "")).lower()
    failed = result is None or bool(result.get("is_error"))
    if failed and any(h in blob for h in _AUTH_HINTS):
        raise ClaudeUnavailable("Claude Code is not installed or not signed in: %s" % (text or err or out).strip()[:500])
    if failed and any(h in blob for h in _RATE_HINTS):
        raise RateLimited("Claude Code was refused for load or usage limits: %s" % (text or err or out).strip()[:500])
    if result is None:
        raise TranslateError("Claude Code ended without a result (exit code %d). It printed:\n%s"
                             % (code, (err.strip() or out.strip() or "(nothing)")[:1500]))
    turn = {
        "resume": resume, "seconds": seconds, "exit_code": code,
        "subtype": result.get("subtype"), "is_error": result.get("is_error"),
        "num_turns": result.get("num_turns"), "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"), "total_cost_usd": result.get("total_cost_usd"),
        "usage": result.get("usage"), "models": sorted((result.get("modelUsage") or {}).keys()),
        "model_usage": result.get("modelUsage"), "permission_denials": result.get("permission_denials"),
        "reply": text[:500],
    }
    log("claude %s: %s in %.0fs, %s turns%s" % (
        "follow-up" if resume else "run", turn["subtype"] or "finished", seconds, turn["num_turns"],
        (", reported cost $%.2f" % turn["total_cost_usd"]) if isinstance(turn["total_cost_usd"], (int, float)) else ""))
    if result.get("is_error") and turn["subtype"] not in ("error_max_turns",):
        raise TranslateError("Claude Code reported an error (%s): %s" % (turn["subtype"], (text or err).strip()[:1000]))
    return turn


def _model_problems(page_dir, page):
    page_dir = Path(page_dir)
    if not (page_dir / "units.json").is_file():
        return ["units.json was not written."]
    try:
        units = json.loads((page_dir / "units.json").read_text(encoding="utf-8-sig"))
    except ValueError as e:
        return ["units.json is not valid JSON: %s" % e]
    return units_mod.validate(units, page, model_part=True)


# --------------------------------------------------------------------------------------------- pre-pass and merge
PREPASS_KEYS = ("done", "origin", "rule", "keep", "role", "en", "group")


def _clear_marks(page):
    for l in page["lines"]:
        for k in PREPASS_KEYS:
            l.pop(k, None)


def _write_json(path, obj):
    tmp = Path(str(path) + ".part")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _merge(page, model_units, groups):
    """One units.json: rule and memory units plus the model's, in reading order (by first line id; units read from
    the image stay where the model put them)."""
    pre = []
    keep = []
    for l in page["lines"]:
        if l.get("done") and l.get("origin") == "rule":
            if l.get("keep"):
                keep.append(l["id"])
            else:
                pre.append({"id": "r%d" % l["id"], "lines": [l["id"]], "role": l["role"], "en": l["en"], "origin": "rule"})
    for g in groups:
        u = {"id": g["id"], "lines": g["lines"], "role": g["role"], "en": g["en"], "origin": "memory"}
        if g.get("en_short"):
            u["en_short"] = g["en_short"]
        pre.append(u)
    taken = {u.get("id") for u in (model_units or {}).get("units", [])}
    for u in pre:
        while u["id"] in taken:
            u["id"] += "x"
    merged = []
    for u in (model_units or {}).get("units", []):
        u = dict(u)
        u.setdefault("origin", "model")
        merged.append(u)
    for u in sorted(pre, key=lambda u: u["lines"][0]):
        at = next((k for k, m in enumerate(merged) if m.get("lines") and m["lines"][0] > u["lines"][0]), len(merged))
        merged.insert(at, u)
    out = {"units": merged,
           "keep": sorted(set((model_units or {}).get("keep", [])) | set(keep)),
           "drop": sorted((model_units or {}).get("drop", [])),
           "notes": list((model_units or {}).get("notes", []))}
    return out


def _usage_summary(attempts):
    tot = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
    for a in attempts:
        for k in tot:
            tot[k] += ((a.get("usage") or {}).get(k) or 0)
    return tot


def _translate_claude(page_dir, log, minutes=None, model=None, max_turns=None, effort=None, force=False, memory=True):
    page_dir = Path(page_dir).resolve()
    t_start = time.time()
    prompt_text = PROMPT_FILE.read_text(encoding="utf-8")
    for need in ("page.json", "page.png"):
        if not (page_dir / need).is_file():
            raise TranslateError("%s is missing from %s; run the extract step first." % (need, page_dir))
    page = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
    _clear_marks(page)
    digests = memory_mod.image_digests(page)
    h = memory_mod.page_hash(page, prompt_text, digests)
    pdf_name = Path(str(page.get("source", ""))).name
    page_no = page.get("page_index", -1) + 1
    record = {"backend": "claude", "page_hash": h, "lines_total": len(page["lines"]), "by_rule": 0, "by_memory": 0,
              "sent_to_model": 0, "reused_page": None, "memory": memory, "attempts": []}

    def finish(units, how):
        record["how"] = how
        record["seconds"] = round(time.time() - t_start, 1)
        record["model_seconds"] = round(sum(a.get("seconds") or 0 for a in record["attempts"]), 1)
        record["tokens"] = _usage_summary(record["attempts"])
        record["cost_usd"] = round(sum(a.get("total_cost_usd") or 0 for a in record["attempts"]), 4)
        _write_json(page_dir / RUN_RECORD, record)
        return units

    # 1. this folder already holds a valid translation of this very page
    if not force and (page_dir / "units.json").is_file():
        try:
            units = units_mod.load_units(page_dir)
        except ValueError:
            units = None
        if units is not None and units.get("page_hash") in (None, h):
            log("units.json already valid for this page; not translated again")
            old = page_dir / RUN_RECORD
            if old.is_file():
                return units     # keep the record of the run that made it
            return finish(units, "existing")

    # 2. the same page was translated before (this PDF or another edition)
    if memory and not force:
        rec = memory_mod.find_page(h)
        if rec and Path(rec["units"]).resolve() != (page_dir / "units.json").resolve():
            try:
                units = json.loads(Path(rec["units"]).read_text(encoding="utf-8"))
                problems = units_mod.validate(units, page)
            except (OSError, ValueError) as e:
                problems = [str(e)]
            if not problems:
                units["page_hash"] = h
                _write_json(page_dir / "page.json", page)
                _write_json(page_dir / "units.json", units)
                record["reused_page"] = {"from": rec["units"], "pdf": rec.get("pdf"), "page": rec.get("page")}
                log("same page as %s page %s: translation reused" % (rec.get("pdf"), rec.get("page")))
                return finish(units, "reused page")
            log("an earlier translation of this page exists but does not fit; translating")

    # 3. pre-pass: rules, then memory
    counts = rules_mod.apply(page)
    record["rules"] = counts
    record["by_rule"] = sum(counts.values())
    groups = memory_mod.apply(page) if memory else []
    record["by_memory"] = sum(len(g["lines"]) for g in groups)
    record["memory_sources"] = sorted({"%s p%s" % (g.get("pdf"), g.get("page")) for g in groups})
    left = [l for l in page["lines"] if not l.get("done")]
    record["sent_to_model"] = len(left)
    _write_json(page_dir / "page.json", page)
    log("pre-pass: %d lines, %d by rule, %d from memory, %d for the model"
        % (len(page["lines"]), record["by_rule"], record["by_memory"], len(left)))

    old = page_dir / "units.json"
    if old.exists():
        old.replace(page_dir / "units.previous.json")
    needs_model = bool(left) or bool(page.get("outlined")) or not page["lines"]
    model_units = None
    if needs_model:
        try:
            model_units = _call_model(page_dir, page, prompt_text, log, record, minutes, model, max_turns, effort)
        except BaseException as e:
            record["error"] = str(e)[:2000]
            finish(None, "failed")
            raise
        _write_json(page_dir / "units.model.json", model_units)
    else:
        log("every line settled by rule or memory: no model call")
    units = _merge(page, model_units, groups)
    units["page_hash"] = h
    problems = units_mod.validate(units, page)
    if problems:
        _write_json(page_dir / "units.merged-invalid.json", units)
        finish(None, "merge failed")
        raise TranslateError("The merged units do not pass the checks (a program error, not the model's):\n"
                             + "\n".join("- " + p for p in problems[:20]))
    _write_json(page_dir / "units.json", units)
    if memory and needs_model:
        record["stored_in_memory"] = memory_mod.store(units, page, {
            "pdf": pdf_name, "page": page_no, "backend": "claude",
            "model": ",".join(sorted({m for a in record["attempts"] for m in a.get("models", [])})) or model})
        memory_mod.record_page(h, page_dir / "units.json", pdf_name, page_no)
    elif memory:
        memory_mod.record_page(h, page_dir / "units.json", pdf_name, page_no)
    return finish(units, "translated")


def _call_model(page_dir, page, prompt_text, log, record, minutes, model, max_turns, effort):
    binary = find_claude()
    record["auth"] = check_signed_in(binary)
    opts = {"model": model or os.environ.get("REPAINT_CLAUDE_MODEL") or None,
            "effort": effort or os.environ.get("REPAINT_CLAUDE_EFFORT") or None,
            "max_turns": int(max_turns or DEFAULT_MAX_TURNS)}
    minutes = float(minutes or os.environ.get("REPAINT_CLAUDE_MINUTES") or DEFAULT_MINUTES)
    record.update({"binary": str(binary), "model": opts["model"], "effort": opts["effort"], "minutes_limit": minutes,
                   "max_turns": opts["max_turns"]})
    try:
        record["claude_version"] = _run_child([binary, "--version"], page_dir, child_env(), None, 60)[1].strip()[:80]
    except (OSError, TranslateError):
        pass
    before = {p.name for p in page_dir.iterdir()}
    deadline = time.time() + minutes * 60
    n_done = sum(1 for l in page["lines"] if l.get("done"))
    turn = None
    for attempt in (1, 2):
        session = str(uuid.uuid4())       # each page and attempt has its own conversation
        record["session_id"] = session
        try:
            log("claude: translating %s (session %s)" % (page_dir.name, session[:8]))
            turn = _one_turn(binary, page_dir, prompt_text, _first_message(page_dir, n_done), session, False, opts,
                             deadline - time.time(), log)
            break
        except RateLimited as e:
            record.setdefault("rate_limited", []).append(str(e)[:300])
            if attempt == 2 or deadline - time.time() < 180:
                raise
            log("claude: refused for load or usage limits; waiting 60 s and trying once more")
            if _stopping.wait(60):
                raise TranslateError("Stopped while waiting to retry.")
            for f in ("units.json",):
                try:
                    (page_dir / f).unlink()
                except OSError:
                    pass
    problems = _model_problems(page_dir, page)
    turn["problems"] = problems
    record["attempts"].append(turn)
    record["valid_first_time"] = not problems
    if problems:
        log("claude: units.json has %d problem%s; asking once for a corrected file"
            % (len(problems), "" if len(problems) == 1 else "s"))
        left = deadline - time.time()
        if left < 60:
            raise TranslateError("No time left for the follow-up turn; units.json has these problems:\n"
                                 + "\n".join("- " + p for p in problems))
        turn = _one_turn(binary, page_dir, prompt_text, _fix_message(page_dir, problems), session, True, opts, left, log)
        problems = _model_problems(page_dir, page)
        turn["problems"] = problems
        record["attempts"].append(turn)
    record["extra_files"] = sorted({p.name for p in page_dir.iterdir()} - before - {"units.json"})
    if problems:
        raise TranslateError(units_mod.format_problems(problems, page_dir / "units.json")
                             + "\n(after one corrective follow-up)")
    units = json.loads((page_dir / "units.json").read_text(encoding="utf-8-sig"))
    for u in units.get("units", []):
        u["origin"] = "model"
    return units


# --------------------------------------------------------------------------------------------- entry point
def translate_page(page_dir, backend, on_log=None, **options):
    """Produce and return the validated units for PAGE_DIR with the named backend (files | claude).

    options for claude: minutes (time limit, default 15), model, effort (default: Claude Code's own),
    max_turns (default 40), force (translate again even if a valid units.json for this page exists),
    memory (default True: use and feed translations/_memory).
    Raises ValueError (files: units.json missing or invalid), TranslateError / RateLimited / ClaudeUnavailable."""
    log = on_log or (lambda m: None)
    if backend == "files":
        units = units_mod.load_units(page_dir)
        log("files: units.json is valid (%d units)" % len(units["units"]))
        return units
    if backend == "claude":
        return _translate_claude(page_dir, log, **options)
    raise ValueError("Unknown translator %r; use one of: %s." % (backend, ", ".join(BACKENDS)))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Write and check units.json for one page folder.")
    ap.add_argument("page_dir")
    ap.add_argument("--backend", choices=BACKENDS, default="files")
    ap.add_argument("--minutes", type=float, default=None, help="time limit for claude (default 15)")
    ap.add_argument("--model", default=None, help="model for claude (default: Claude Code's default)")
    ap.add_argument("--effort", default=None, choices=EFFORTS, help="effort for claude (default: Claude Code's own)")
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="translate again even if a valid units.json exists")
    ap.add_argument("--no-memory", action="store_true", help="do not use or feed the translation memory")
    a = ap.parse_args(argv)
    opts = {k: v for k, v in (("minutes", a.minutes), ("model", a.model), ("max_turns", a.max_turns), ("effort", a.effort))
            if v is not None}
    if a.backend == "claude":
        opts.update(force=a.force, memory=not a.no_memory)
    try:
        units = translate_page(a.page_dir, a.backend, on_log=lambda m: print(m, flush=True), **opts)
    except KeyboardInterrupt:
        stop_all()
        print("stopped", flush=True)
        return 2
    except (ValueError, TranslateError) as e:
        print(str(e), flush=True)
        return 1
    print("ok: %d units" % len(units["units"]), flush=True)
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    sys.exit(main())
