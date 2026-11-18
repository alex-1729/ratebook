"""Translate pages of a Japanese brochure PDF into English, keeping each page's layout.

    .venv/bin/python scripts/repaint/run.py SRC.pdf (--pages 4,9 | --pages 3-6 | --all) --work DIR --out OUT.pdf
        [--translator files|claude] [--jobs N] [--layout-jobs N] [--first 7[,8]] [--page-dir-out DIR] [--force]
        [--no-memory]
        [--minutes M] [--model NAME] [--effort low|medium|high|xhigh|max]

Pages are numbered from 1, as a person says them; page N is worked on in DIR/pNNN (p004 for page 4).
For each page: extract (page.json, page.png; skipped if page.json exists), translate (units.json; see translate.py:
a valid units.json for the same page is kept, an identical page translated before is reused, rules and the
translation memory settle what they can, the model does the rest), layout (english.pdf), checks (checks.json).
--force redoes extract and translate.

Pages are extracted in order (--first pages before the rest) and handed to up to --jobs translators at once
(default 4 for claude). As soon as a page's translation is ready it is laid out and checked (layout.py and
checks.py run as child processes, up to --layout-jobs pages at once, default half the processors, at most 6), and with
--page-dir-out its English page is copied to DIR/pNNN.pdf. At the end the English pages are put together into
OUT.pdf in page order; a page that failed or has no translation goes in as the original page. OUT.json beside
OUT.pdf lists every page: status ok | review | failed | untranslated, the checks verdict, errors, warnings, translator
used and how (lines by rule / memory / model), seconds and problems, plus run totals.

Output: one line per stage change for a calling app,
    @@progress {"page": 4, "of": 2, "n": 1, "stage": "translate", "state": "done", "message": "..."}
stage is extract | translate | layout | check | page | assemble. "page" "done" marks a page finished, with
"status" and "pdf" (its DIR/pNNN.pdf, or null). page is the page number, n its place in the work order and of the
page count (page is null for "assemble"). Everything else is plain log text. Exit code 0 when OUT.pdf was written
(even if pages need review), 2 when nothing usable was produced, on bad arguments, or when stopped.

layout.py and checks.py are imported only when a page first needs them. If one is not there, the pages are still
translated and the run says which stage was missing.
"""
import argparse
import concurrent.futures as cf
import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import translate as translate_mod  # noqa: E402
import units as units_mod  # noqa: E402

EXTRACT = HERE / "extract.py"
STAGE_TIMEOUT = 900   # seconds for layout.py or checks.py on one page
OK_VERDICTS = {"ok", "pass", "passed", "good", "clean"}
_print_lock = threading.Lock()


# --------------------------------------------------------------------------------------------- output
def say(text):
    with _print_lock:
        sys.stdout.write(text.rstrip("\n") + "\n")
        sys.stdout.flush()


def progress(page, of, n, stage, state, message="", **extra):
    d = {"page": page, "of": of, "n": n, "stage": stage, "state": state, "message": message}
    d.update(extra)
    say("@@progress " + json.dumps(d, ensure_ascii=False))


# --------------------------------------------------------------------------------------------- children
_children = set()
_children_lock = threading.Lock()


def run_child(args, timeout=600):
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.Popen([str(a) for a in args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=env, **kw)
    with _children_lock:
        _children.add(p)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise RuntimeError("timed out after %d s" % timeout)
    finally:
        with _children_lock:
            _children.discard(p)
    return p.returncode, out.decode("utf-8", "replace")


def stop_children():
    translate_mod.stop_all()
    with _children_lock:
        procs = list(_children)
    for p in procs:
        try:
            p.terminate()
        except OSError:
            pass
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def _sigterm(signum, frame):
    raise KeyboardInterrupt()


# --------------------------------------------------------------------------------------------- arguments
def parse_pages(spec, count):
    pages = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()) or int(a) > int(b):
                raise ValueError("'%s' is not a page range like 3-6." % part)
            pages.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            pages.add(int(part))
        else:
            raise ValueError("'%s' is not a page number." % part)
    if not pages:
        raise ValueError("No pages given.")
    bad = [p for p in sorted(pages) if p < 1 or p > count]
    if bad:
        raise ValueError("Page%s %s not in the PDF, which has %d page%s (numbered from 1)."
                         % ("s" if len(bad) > 1 else "", ", ".join(map(str, bad)), count, "" if count == 1 else "s"))
    return sorted(pages)


def load_stage(module, func):
    """Import scripts/repaint/<module>.py lazily; returns (function, None) or (None, reason)."""
    if not (HERE / (module + ".py")).is_file():
        return None, "%s.py is not there yet" % module
    try:
        mod = importlib.import_module(module)
    except Exception as e:   # noqa: BLE001 - any import failure means the stage is not usable
        return None, "%s.py could not be loaded (%s: %s)" % (module, type(e).__name__, e)
    f = getattr(mod, func, None)
    if not callable(f):
        return None, "%s.py has no %s()" % (module, func)
    return f, None


# --------------------------------------------------------------------------------------------- the run
class Page:
    def __init__(self, number, n, of, work):
        self.number, self.n, self.of = number, n, of
        self.dir = work / ("p%03d" % number)
        self.rec = {"index": number - 1, "page": number, "folder": str(self.dir), "status": None, "verdict": None,
                    "errors": [], "warnings": [], "backend": None, "seconds": 0.0, "problems": [], "stages": {}}
        self.failed = False
        self.untranslated = False

    def log(self, text):
        for line in str(text).splitlines() or [""]:
            say("p%03d: %s" % (self.number, line))

    def stage(self, name, fn):
        """Run one stage, with progress lines and timing. Returns fn's result, or None if it failed."""
        progress(self.number, self.of, self.n, name, "start")
        t0 = time.time()
        try:
            result, msg = fn()
        except KeyboardInterrupt:
            raise
        except translate_mod.ClaudeUnavailable as e:
            # Not this page's fault: every page would fail the same way, so the whole run stops.
            self.rec["stages"][name] = {"state": "failed", "seconds": round(time.time() - t0, 1)}
            self.failed = True
            self.rec["problems"].append("%s: %s" % (name, e))
            progress(self.number, self.of, self.n, name, "failed", str(e).splitlines()[0][:300])
            raise
        except Exception as e:   # noqa: BLE001 - one page's failure must not stop the others
            dt = round(time.time() - t0, 1)
            self.rec["seconds"] = round(self.rec["seconds"] + dt, 1)
            self.rec["stages"][name] = {"state": "failed", "seconds": dt}
            msg = str(e) or type(e).__name__
            if not isinstance(e, (ValueError, translate_mod.TranslateError, RuntimeError)):
                msg = "%s: %s" % (type(e).__name__, msg)
                self.log(traceback.format_exc())
            self.failed = True
            self.rec["problems"].append("%s: %s" % (name, msg))
            self.log("%s failed: %s" % (name, msg))
            progress(self.number, self.of, self.n, name, "failed", " ".join(msg.splitlines()[:2])[:300])
            return None
        dt = round(time.time() - t0, 1)
        self.rec["seconds"] = round(self.rec["seconds"] + dt, 1)
        self.rec["stages"][name] = {"state": "done", "seconds": dt}
        progress(self.number, self.of, self.n, name, "done", msg or "")
        return result if result is not None else True


def do_extract(page, src, force):
    def fn():
        if (page.dir / "page.json").is_file() and (page.dir / "page.png").is_file() and not force:
            try:
                old = json.loads((page.dir / "page.json").read_text(encoding="utf-8"))
            except ValueError:
                old = {}
            if old.get("page_index") != page.number - 1 or Path(str(old.get("source", ""))).name != Path(src).name:
                raise RuntimeError("%s holds page.json for %s page index %s, not this PDF's page %d. Use another --work "
                                   "folder, or --force to replace it." % (page.dir, Path(str(old.get("source"))).name,
                                                                          old.get("page_index"), page.number))
            return True, "page.json already there"
        page.dir.mkdir(parents=True, exist_ok=True)
        code, out = run_child([sys.executable, EXTRACT, src, page.number - 1, page.dir])
        if out.strip():
            page.log(out.strip())
        if code != 0 or not (page.dir / "page.json").is_file():
            raise RuntimeError("extract.py failed (exit code %d)" % code)
        return True, out.strip().splitlines()[-1][:200] if out.strip() else "extracted"
    return page.stage("extract", fn)


def do_translate(page, backend, options):
    def fn():
        if backend == "files":
            if not (page.dir / "units.json").is_file():
                page.untranslated = True
                page.rec["backend"] = "files"
                page.rec["problems"].append("translate: no units.json in %s (translator 'files' needs one)" % page.dir)
                return False, "no units.json"
            units = translate_mod.translate_page(page.dir, "files", on_log=page.log)
            page.rec["backend"] = "files"
            return units, "valid units.json (%d units)" % len(units["units"])
        page.rec["backend"] = "claude"
        units = translate_mod.translate_page(page.dir, "claude", on_log=page.log, **options)
        run_rec = page.dir / translate_mod.RUN_RECORD
        if run_rec.is_file():
            try:
                r = json.loads(run_rec.read_text(encoding="utf-8"))
                page.rec["translate_run"] = {k: r.get(k) for k in (
                    "how", "lines_total", "by_rule", "by_memory", "sent_to_model", "valid_first_time", "model_seconds",
                    "tokens", "cost_usd", "model", "effort", "rate_limited", "reused_page")}
                page.rec["translate_run"]["follow_ups"] = max(0, len(r.get("attempts", [])) - 1)
                page.rec["backend"] = "claude (%s)" % r.get("how", "?")
            except ValueError:
                pass
        tr = page.rec.get("translate_run") or {}
        return units, "%d units (%s: %s rule, %s memory, %s model)" % (
            len(units["units"]), tr.get("how", "?"), tr.get("by_rule", 0), tr.get("by_memory", 0), tr.get("sent_to_model", 0))
    t0 = time.time()
    result = page.stage("translate", fn)
    page.rec["translate_window"] = [round(t0 - RUN_T0, 1), round(time.time() - RUN_T0, 1)]
    return None if result is False else result


def _stage_child(page, script, args, what):
    """Run layout.py / checks.py for one page as a child process (so several pages can run at once, and a crash
    in one page cannot take the runner down). Returns its last output line."""
    code, out = run_child([sys.executable, HERE / script, page.dir] + args, timeout=STAGE_TIMEOUT)
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if code != 0:
        tail = "\n".join(lines[-12:]) or "(no output)"
        raise RuntimeError("%s exited with code %d:\n%s" % (what, code, tail))
    return lines[0] if lines else ""


def do_layout(page, _available=None):
    def fn():
        pdf = page.dir / "english.pdf"
        if pdf.exists():
            pdf.unlink()
        first = _stage_child(page, "layout.py", ["--out", pdf], "layout.py")
        if not pdf.is_file():
            raise RuntimeError("layout did not write english.pdf")
        page.rec["english_pdf"] = str(pdf)
        return True, first.replace(str(page.dir), page.dir.name)[:200] or "english.pdf written"
    return page.stage("layout", fn)


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def do_check(page, check_page):
    def fn():
        cj = page.dir / "checks.json"
        if cj.exists():
            cj.unlink()
        _stage_child(page, "checks.py", [], "checks.py")
        r = json.loads(cj.read_text(encoding="utf-8"))
        r = r if isinstance(r, dict) else {}
        s = r.get("summary") if isinstance(r.get("summary"), dict) else r   # checks.py puts them under "summary"
        verdict = s.get("verdict")
        page.rec["verdict"] = verdict
        # counts as checks.py reports them (a list is kept as given); details are in the page's checks.json
        page.rec["errors"] = s.get("errors") if isinstance(s.get("errors"), (int, list)) else _as_list(s.get("errors"))
        page.rec["warnings"] = s.get("warnings") if isinstance(s.get("warnings"), (int, list)) else _as_list(s.get("warnings"))
        if s.get("by_check"):
            page.rec["by_check"] = s["by_check"]
        page.rec["checks_json"] = str(page.dir / "checks.json")
        return r, "verdict: %s, %s errors, %s warnings" % (verdict, page.rec["errors"], page.rec["warnings"])
    t0 = time.time()
    ok = page.stage("check", fn)
    if ok is None and page.rec.get("english_pdf"):
        # the English page exists; a crash in the checks means it has not been checked, not that it failed
        page.failed = False
        page.check_crashed = True
        page.rec["problems"].append("the English page was not checked; review it")
    return ok


def finish_status(page, checks_ran):
    if page.failed:
        page.rec["status"] = "failed"
    elif page.untranslated:
        page.rec["status"] = "untranslated"
    elif checks_ran and not getattr(page, "check_crashed", False) and str(page.rec["verdict"]).lower() in OK_VERDICTS:
        page.rec["status"] = "ok"
    else:
        page.rec["status"] = "review"


def publish_page(page, out_dir):
    """Copy a finished English page to OUT_DIR/pNNN.pdf at once, for a viewer showing pages as they arrive."""
    if not out_dir or page.rec["status"] not in ("ok", "review") or not page.rec.get("english_pdf"):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / ("p%03d.pdf" % page.number)
    tmp = out_dir / ("p%03d.pdf.part" % page.number)
    shutil.copyfile(page.rec["english_pdf"], str(tmp))
    os.replace(str(tmp), str(dst))
    page.rec["page_pdf"] = str(dst)
    return dst


def assemble(src, pages, out_pdf):
    import fitz
    srcdoc = fitz.open(str(src))
    out = fitz.open()
    english = 0
    for p in sorted(pages, key=lambda p: p.number):
        pdf = p.rec.get("english_pdf")
        if p.rec["status"] in ("ok", "review") and pdf and Path(pdf).is_file():
            with fitz.open(pdf) as e:
                out.insert_pdf(e, from_page=0, to_page=0)
            english += 1
            p.rec["output_page"] = "english"
        else:
            out.insert_pdf(srcdoc, from_page=p.number - 1, to_page=p.number - 1)
            p.rec["output_page"] = "original"
    if english == 0:
        return 0
    tmp = out_pdf.with_name(out_pdf.name + ".part")
    out.save(str(tmp), garbage=3, deflate=True)
    out.close()
    os.replace(str(tmp), str(out_pdf))
    return english


def max_concurrency(pages):
    ev = []
    for p in pages:
        w = p.rec.get("translate_window")
        if w and p.rec.get("translate_run", {}).get("model_seconds"):
            ev += [(w[0], 1), (w[1], -1)]
    cur = best = 0
    for _, d in sorted(ev, key=lambda e: (e[0], e[1])):
        cur += d
        best = max(best, cur)
    return best


def write_report(path, meta, pages):
    meta = dict(meta, pages=[p.rec for p in sorted(pages, key=lambda p: p.number)],
                summary={s: sum(1 for p in pages if p.rec["status"] == s) for s in ("ok", "review", "failed", "untranslated")})
    meta["seconds"] = round(time.time() - RUN_T0, 1)
    meta["model_calls_at_once_max"] = max_concurrency(pages)
    tr = [p.rec.get("translate_run") or {} for p in pages]
    meta["lines"] = {k: sum(t.get(k) or 0 for t in tr) for k in ("lines_total", "by_rule", "by_memory", "sent_to_model")}
    meta["pages_reused_whole"] = sum(1 for t in tr if t.get("how") == "reused page")
    meta["cost_usd_reported"] = round(sum(t.get("cost_usd") or 0 for t in tr), 2)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(path))


RUN_T0 = time.time()


def main(argv=None):
    global RUN_T0
    RUN_T0 = time.time()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="Translate brochure pages into English, keeping the layout.")
    ap.add_argument("src", help="the Japanese PDF")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pages", help="page numbers from 1: 4,9 or 3-6 or 1,3-5")
    g.add_argument("--all", action="store_true", help="every page")
    ap.add_argument("--work", required=True, help="folder for the per-page work (pNNN folders)")
    ap.add_argument("--out", required=True, help="the English PDF to write")
    ap.add_argument("--translator", choices=translate_mod.BACKENDS, default="files")
    ap.add_argument("--force", action="store_true", help="redo extract and translate even if their files exist")
    ap.add_argument("--jobs", type=int, default=None, help="pages translated at once (default 4 for claude, 1 otherwise)")
    ap.add_argument("--layout-jobs", type=int, default=None,
                    help="pages laid out and checked at once (default: half the processors, at most 6)")
    ap.add_argument("--first", default=None, help="pages to translate first, e.g. 7 or 7,8 (the rest follow in order)")
    ap.add_argument("--page-dir-out", default=None, help="also write each finished English page to DIR/pNNN.pdf at once")
    ap.add_argument("--minutes", type=float, default=None, help="time limit per page for claude (default 15)")
    ap.add_argument("--model", default=None, help="model for claude (default: $REPAINT_CLAUDE_MODEL, else Claude Code's default)")
    ap.add_argument("--effort", default=None, choices=translate_mod.EFFORTS, help="effort for claude (default: Claude Code's own)")
    ap.add_argument("--no-memory", action="store_true", help="do not use or feed the translation memory and page reuse")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0

    src = Path(a.src).resolve()
    if not src.is_file():
        say("The PDF %s was not found." % src)
        return 2
    try:
        import fitz
        with fitz.open(str(src)) as d:
            count = d.page_count
        numbers = list(range(1, count + 1)) if a.all else parse_pages(a.pages, count)
        first = parse_pages(a.first, count) if a.first else []
    except ValueError as e:
        say(str(e))
        return 2
    except Exception as e:   # noqa: BLE001
        say("The PDF %s could not be opened: %s" % (src, e))
        return 2
    jobs = a.jobs if a.jobs is not None else (4 if a.translator == "claude" else 1)
    layout_jobs = a.layout_jobs or max(1, min(6, (os.cpu_count() or 2) // 2))
    if jobs < 1 or layout_jobs < 1:
        say("--jobs and --layout-jobs must be 1 or more.")
        return 2
    work = Path(a.work).resolve()
    out_pdf = Path(a.out).resolve()
    out_json = out_pdf.with_suffix(".json")
    page_out = Path(a.page_dir_out).resolve() if a.page_dir_out else None
    work.mkdir(parents=True, exist_ok=True)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    options = {k: v for k, v in (("minutes", a.minutes), ("model", a.model), ("effort", a.effort)) if v is not None}
    if options and a.translator != "claude":
        say("--minutes, --model and --effort apply only to --translator claude; ignored.")
        options = {}
    if a.translator == "claude":
        options.update(force=a.force, memory=not a.no_memory)

    order = [n for n in first if n in numbers] + [n for n in numbers if n not in first]
    pages = [Page(n, i + 1, len(numbers), work) for i, n in enumerate(order)]
    meta = {"source": str(src), "output": str(out_pdf), "work": str(work), "translator": a.translator, "jobs": jobs, "layout_jobs": layout_jobs,
            "model": a.model or os.environ.get("REPAINT_CLAUDE_MODEL"), "effort": a.effort, "memory": not a.no_memory,
            "pages_requested": numbers, "order": order, "page_dir_out": str(page_out) if page_out else None,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "stopped_after": None, "message": None}
    say("%s: %d page%s (%s), translator %s, %d at once, work folder %s" % (
        src.name, len(numbers), "" if len(numbers) == 1 else "s", a.pages or "all", a.translator, jobs, work))
    try:
        # A parent that started us in the background may have set Ctrl-C to "ignore"; we always want it.
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, _sigterm)
    except (ValueError, AttributeError):
        pass

    try:
        return _run(a, src, pages, meta, options, jobs, out_pdf, out_json, page_out, layout_jobs)
    except KeyboardInterrupt:
        say("Stopping: ending child processes ...")
        stop_children()
        meta["stopped_after"] = "interrupted"
        meta["message"] = "Stopped by the user before the run finished."
        for p in pages:
            if p.rec["status"] is None:
                p.rec["status"] = "failed"
                p.rec["problems"].append("stopped before this page was finished")
        write_report(out_json, meta, pages)
        say("Stopped. Report so far: %s" % out_json)
        return 2


def _run(a, src, pages, meta, options, jobs, out_pdf, out_json, page_out, layout_jobs):
    """Extract in order, translate up to `jobs` pages at once, and lay out and check each page as soon as its
    translation is ready (up to `layout_jobs` pages at once, each in its own child process)."""
    stage_fns = {}
    stage_lock = threading.Lock()

    def stage_fn(name, func):
        with stage_lock:
            if name not in stage_fns:
                stage_fns[name] = load_stage(name, func)
            return stage_fns[name]

    fatal, rate_limited = [], []
    ex = cf.ThreadPoolExecutor(max_workers=jobs)
    futures = {}

    def job(p):
        if fatal or len(rate_limited) >= 2:
            p.failed = True
            p.rec["problems"].append("translate: not started (%s)" % ("the run is stopping" if fatal else "rate limited"))
            progress(p.number, p.of, p.n, "translate", "failed", "not started")
            return
        try:
            do_translate(p, a.translator, options)
        except translate_mod.ClaudeUnavailable as e:
            fatal.append(str(e))
        if any(str(x).startswith("translate: Claude Code was refused for load or usage limits") for x in p.rec["problems"]):
            rate_limited.append(p.number)

    def finish_page(p):
        """Layout, checks and per-page output for one translated page."""
        if not p.failed and not p.untranslated:
            layout_page, why = stage_fn("layout", "layout_page")
            if layout_page is None:
                p.rec["problems"].append("layout not run: " + why)
            else:
                if do_layout(p, layout_page) is not None:
                    check_page, why_c = stage_fn("checks", "check_page")
                    if check_page is None:
                        p.rec["problems"].append("checks not run: " + why_c)
                        p.check_crashed = True
                    else:
                        do_check(p, check_page)
        finish_status(p, stage_fns.get("checks", (None,))[0] is not None)
        dst = publish_page(p, page_out)
        progress(p.number, p.of, p.n, "page", "done", p.rec["status"], status=p.rec["status"],
                 pdf=str(dst) if dst else None)

    lay_ex = cf.ThreadPoolExecutor(max_workers=layout_jobs)
    lay_futures = []
    try:
        # 1. extract in the requested order, handing each page to the translators at once
        finished = set()
        for p in pages:
            if do_extract(p, src, a.force) is not None:
                futures[ex.submit(job, p)] = p
            else:
                lay_futures.append(lay_ex.submit(finish_page, p))
            for f in [f for f in futures if f.done() and f not in finished]:   # pages already translated
                finished.add(f)
                f.result()
                lay_futures.append(lay_ex.submit(finish_page, futures[f]))
        # 2. as translations finish, lay out and check those pages
        pending = set(futures) - finished
        while pending:
            done, pending = cf.wait(pending, timeout=0.5, return_when=cf.FIRST_COMPLETED)
            for f in sorted(done, key=lambda f: futures[f].n):
                f.result()
                lay_futures.append(lay_ex.submit(finish_page, futures[f]))
            if fatal:
                for f in pending:
                    f.cancel()
        ex.shutdown(wait=True)
        while not all(f.done() for f in lay_futures):
            cf.wait(lay_futures, timeout=0.5)
        for f in lay_futures:
            f.result()
        lay_ex.shutdown(wait=True)
    except KeyboardInterrupt:
        ex.shutdown(wait=False, cancel_futures=True)
        lay_ex.shutdown(wait=False, cancel_futures=True)
        raise

    if fatal:
        say(fatal[0])
        meta["stopped_after"], meta["message"] = "translate", fatal[0]
    elif len(rate_limited) >= 2:
        meta["message"] = "Stopped sending pages to Claude: pages %s were refused for load or usage limits." % (
            ", ".join(map(str, rate_limited)))
        say(meta["message"])
    for p in pages:
        if p.rec["status"] is None:
            finish_status(p, False)
    layout_page, why_layout = stage_fns.get("layout", (None, None))
    if layout_page is None and why_layout:
        msg = "The layout stage is not available (%s); no English PDF was made." % why_layout
        say(msg)
        meta["stopped_after"], meta["message"] = meta["stopped_after"] or "translate", msg
        write_report(out_json, meta, pages)
        say("Report: %s" % out_json)
        return 2

    # 3. assemble
    progress(None, len(pages), None, "assemble", "start")
    try:
        english = assemble(src, pages, out_pdf)
    except Exception as e:   # noqa: BLE001
        progress(None, len(pages), None, "assemble", "failed", str(e)[:300])
        say("Could not write %s: %s" % (out_pdf, e))
        meta["message"] = "assemble failed: %s" % e
        write_report(out_json, meta, pages)
        return 2
    if english == 0:
        progress(None, len(pages), None, "assemble", "failed", "no page has an English version")
        say("No page has an English version, so %s was not written." % out_pdf)
        meta["message"] = ((meta["message"] or "") + " No English page was produced.").strip()
        write_report(out_json, meta, pages)
        return 2
    write_report(out_json, meta, pages)
    progress(None, len(pages), None, "assemble", "done", "%d of %d pages in English" % (english, len(pages)))
    for p in sorted(pages, key=lambda p: p.number):
        if p.rec["status"] != "ok":
            say("p%03d: %s%s" % (p.number, p.rec["status"],
                                 (" (original page used)" if p.rec.get("output_page") == "original" else "")))
    say("Wrote %s and %s (%.0f s)" % (out_pdf, out_json, time.time() - RUN_T0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
