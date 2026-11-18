"""Add English titles, categories and short summaries to collected news items.

Usage:
    python3 scripts/news_enrich.py                  # every item that has no category yet, then summaries
    python3 scripts/news_enrich.py --only dfl,fsa   # some owners only
    python3 scripts/news_enrich.py --no-summaries   # titles and categories only
    python3 scripts/news_enrich.py --limit 80       # at most this many items (for a trial)

Two passes, both run by the `claude` program (Claude Code) headless, with your existing sign-in and no API key:

1. Classify (titles only, 40 items a call): English title, category, importance, whether the item is relevant
   to the tracker, the tracked products and stories it concerns, and for market-wide sources (regulator,
   association) which insurers it names.
2. Summarise (one call per item, only items marked importance "high" that have no summary): the release is
   fetched once, read, and described in one to three sentences. The fetched page or PDF is held in a temporary
   folder and deleted; only the summary is stored.

What is sent: the item's date, Japanese title, source and URL, the names of the tracked insurers, products and
stories, and for pass 2 the text of the public release. Nothing about you or this machine is added. Claude Code
is started with a replacement system prompt, no settings, no MCP servers and no tools (pass 2: Read only,
confined to the temporary folder).

Fields written to the item: title_en, category, importance, relevant, product_ids, story_ids, insurer_ids (only
for owners that are not insurers), summary_en, enriched {by, date}. An item that already has a category is left
alone unless --redo is given. Titles, dates and links are never changed.

Output uses the same "@@status" lines as news.py, with the owner id "enrich".
"""

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import common
import news

BATCH = 40
MODEL = os.environ.get("NEWS_CLAUDE_MODEL", "sonnet")
SUMMARY_CHARS = 500
write_lock = threading.Lock()


class Unavailable(Exception):
    """Claude Code is missing, not signed in, or refused for usage limits."""


# ---------------------------------------------------------------------------------------------- claude
def find_claude():
    """$NEWS_CLAUDE_BIN or $REPAINT_CLAUDE_BIN, then the copy that ships with the app's Agent SDK, then PATH."""
    for var in ("NEWS_CLAUDE_BIN", "REPAINT_CLAUDE_BIN"):
        env = os.environ.get(var)
        if env:
            if Path(env).is_file():
                return Path(env)
            raise Unavailable("%s is set to %s, but there is no such file." % (var, env))
    sdk = common.ROOT / "app" / "node_modules" / "@anthropic-ai"
    names = ("claude.exe", "claude") if os.name == "nt" else ("claude",)
    folders = [sdk / "claude-agent-sdk"]
    try:
        folders += sorted(p for p in sdk.iterdir() if p.name.startswith("claude-agent-sdk-"))
    except OSError:
        pass
    for folder in folders:
        for name in names:
            if (folder / name).is_file():
                return folder / name
    found = shutil.which("claude")
    if found:
        return Path(found)
    raise Unavailable("Claude Code was not found. Run `npm install` in app/, install Claude Code, or set NEWS_CLAUDE_BIN.")


def child_env():
    env = dict(os.environ)
    for k in list(env):
        if k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT") or k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "CLAUDE_PREVIEW_")):
            del env[k]
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def ask(binary, system, message, cwd, read_files=False, timeout=420):
    """One headless turn. Returns the reply text."""
    args = [str(binary), "-p", "--output-format", "json", "--system-prompt", system,
            "--permission-mode", "dontAsk", "--setting-sources", "",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands", "--model", MODEL]
    if read_files:
        args += ["--tools", "Read", "--allowedTools", "Read", "--restricted", "--max-turns", "6"]
    else:
        args += ["--tools", "", "--max-turns", "1"]
    try:
        proc = subprocess.run(args, input=message.encode("utf-8"), cwd=str(cwd), env=child_env(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude Code did not answer within %d seconds" % timeout)
    out = proc.stdout.decode("utf-8", "replace").strip()
    err = proc.stderr.decode("utf-8", "replace").strip()
    result = None
    for chunk in (out, out.splitlines()[-1] if out else ""):
        try:
            result = json.loads(chunk)
            break
        except ValueError:
            continue
    if isinstance(result, list):
        result = next((m for m in reversed(result) if isinstance(m, dict) and m.get("type") == "result"), None)
    text = (result or {}).get("result") or "" if isinstance(result, dict) else ""
    failed = not isinstance(result, dict) or bool(result.get("is_error"))
    blob = (text + "\n" + err + "\n" + ("" if isinstance(result, dict) else out)).lower()
    if failed and any(h in blob for h in ("not logged in", "/login", "invalid api key", "authentication_error", "oauth token")):
        raise Unavailable("Claude Code is not signed in. Run `claude` once in a terminal, then /login.")
    if failed and any(h in blob for h in ("rate limit", "rate_limit", "usage limit", "limit reached", "overloaded", "429", "529")):
        raise Unavailable("Claude Code was refused for load or usage limits: %s" % (text or err)[:200])
    if failed:
        raise RuntimeError("Claude Code ended without a result: %s" % (text or err or out or "(nothing)")[:300])
    return text


# ---------------------------------------------------------------------------------------------- context
def load_context():
    insurers = {}
    for path in sorted((common.DATA / "insurers").glob("*.json")):
        j = json.loads(path.read_text(encoding="utf-8"))
        insurers[j.get("id") or path.stem] = j
    products = []
    for path in sorted((common.DATA / "products").glob("*.json")):
        j = json.loads(path.read_text(encoding="utf-8"))
        products.append({"id": j.get("id") or path.stem, "insurer": j.get("insurer_id", ""),
                         "name_ja": j.get("name_ja", ""), "name_en": j.get("name_en", ""),
                         "policy_name_ja": j.get("policy_name_ja", "")})
    stories = []
    for path in sorted((news.NEWS / "stories").glob("*.json")) if (news.NEWS / "stories").exists() else []:
        j = json.loads(path.read_text(encoding="utf-8"))
        stories.append({"id": j.get("id") or path.stem, "title_en": j.get("title_en", ""), "title_ja": j.get("title_ja", ""),
                        "insurer_ids": j.get("insurer_ids", []), "started": j.get("started", ""),
                        "summary_en": (j.get("summary_en") or "")[:500]})
    return insurers, products, stories


CLASSIFY = """You label news items for a personal research tracker of Japanese life insurers. The tracker's subject is single-premium savings insurance sold through banks, securities firms and agencies (fixed annuities, indexed annuities, savings-type whole life, tontine-style annuities) and the companies that issue it.

You are given a JSON array of items: id, owner (whose list the item came from), source_kind, date and the Japanese title. Reply with ONE JSON array and nothing else (no code fence, no commentary), one object per item, in the same order:

{"id": the item's id, unchanged,
 "title_en": a faithful, plain English rendering of the title (keep product names in Japanese script followed by an English gloss in brackets only when the gloss is certain; do not add facts that the title does not state),
 "category": one of the category keys below,
 "importance": "high" | "normal" | "low",
 "relevant": true | false,
 "product_ids": ids of tracked products the title names (usually []),
 "story_ids": ids of tracked stories the item belongs to (usually []),
 "insurer_ids": ids of the insurers the title names (only for items whose owner is not an insurer; otherwise omit)}

Categories
- product_launch: a new product or a new edition of one goes on sale
- product_change: terms of an existing product change (limits, currencies, riders, underwriting, fees)
- sales_suspension: sales of a product stop, pause or restart
- rates: crediting rates, assumed rates, dividend rates, premium rate revisions
- regulatory_conduct: a case about a named company or its people: administrative action, misconduct, fraud by staff or agents, data leaks, investigations, apologies, business improvement plans, compliance failures
- regulation: rules and policy for the whole market, not a case against a named company: laws and ordinances, supervisory guidelines, solvency rules, council and working-group meetings, public consultations, industry-wide requests and surveys by the regulator or the association
- distribution: distributors and channels: a bank or agency starts or stops selling, tie-ups
- results: financial results, embedded value, disclosures, ratings, solvency
- corporate: mergers, acquisitions, capital, reorganisation, executive changes, company strategy
- customer_notice: system maintenance, disaster-relief measures, warnings about phishing, procedures, office changes
- other: awards, sponsorship, CSR, surveys, campaigns, recruitment, events

Importance
- high: regulatory_conduct items of substance (orders, findings, admissions, large incidents); a sales suspension; the launch or major revision of a single-premium savings product; mergers and acquisitions; anything belonging to a tracked story
- low: customer_notice and other
- normal: the rest

Relevant
- false for routine items a product researcher would skip: customer_notice and other, and for the regulator's or association's list anything that does not concern life insurance, life insurers or insurance regulation and supervision
- true otherwise

Judge from the title alone. When a title is too vague to classify, use "other" with importance "low" and relevant false, unless the owner is one of the story's insurers and the title could be about the story (apology, 行政処分, 業務改善, 不適切, お詫び), in which case use regulatory_conduct.

Tracked insurers (id: Japanese name / English name):
%(insurers)s

Tracked products (id | insurer | Japanese name | English name):
%(products)s

Tracked stories (id | insurers | started | title | summary):
%(stories)s
"""

SUMMARISE = """You write one short English summary of a public announcement by a Japanese insurer, regulator or industry body, for a personal research tracker.

Reply with the summary only: one to three plain sentences, at most %d characters, in your own words (no quotation). Say who announced what, the dates that matter (on sale from, effective from, period of an order), and the products, channels or companies affected. State only what the document says. Do not name individual employees, customers or victims; executives may be named in their role. No preamble, no bullet points.

If the document cannot be read, is empty, or is plainly not the announcement the headline describes, reply with the single word UNREADABLE.""" % SUMMARY_CHARS


def classify_prompt(insurers, products, stories):
    return CLASSIFY % {
        "insurers": "\n".join("%s: %s / %s" % (i, j.get("name_ja", ""), j.get("name_en", "")) for i, j in insurers.items()),
        "products": "\n".join("%s | %s | %s | %s" % (p["id"], p["insurer"], p["name_ja"], p["name_en"]) for p in products) or "(none)",
        "stories": "\n".join("%s | %s | %s | %s | %s" % (s["id"], ",".join(s["insurer_ids"]), s["started"], s["title_en"], s["summary_en"]) for s in stories) or "(none)",
    }


def parse_array(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("no JSON array in the reply")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------------------------------------- storage
def patch_items(changes):
    """changes: {item_id: {field: value}}. Adds the fields to the items on file."""
    if not changes:
        return
    with write_lock:
        for path in sorted(news.ITEMS.glob("*.jsonl")):
            items = news.read_items(path)
            touched = False
            for item in items:
                c = changes.get(item.get("id"))
                if c:
                    item.update(c)
                    touched = True
            if touched:
                news.write_items(path, items)


def clean_label(raw, item, ctx):
    """Keep only well-formed fields from one reply object."""
    insurers, product_ids, story_ids, owner_is_insurer = ctx
    out = {}
    title = raw.get("title_en")
    if isinstance(title, str) and title.strip():
        out["title_en"] = title.strip()[:400]
    out["category"] = raw.get("category") if raw.get("category") in news.CATEGORIES else "other"
    out["importance"] = raw.get("importance") if raw.get("importance") in ("high", "normal", "low") else "normal"
    out["relevant"] = bool(raw.get("relevant"))
    pids = [p for p in raw.get("product_ids") or [] if p in product_ids]
    if pids:
        out["product_ids"] = pids
    sids = sorted(set([s for s in raw.get("story_ids") or [] if s in story_ids] + list(item.get("story_ids") or [])))
    if sids:
        out["story_ids"] = sids
    if not owner_is_insurer(item):
        ids = [i for i in raw.get("insurer_ids") or [] if i in insurers]
        if ids:
            out["insurer_ids"] = ids
    return out


# ---------------------------------------------------------------------------------------------- passes
def classify(binary, todo, context, jobs, log):
    insurers, products, stories = context
    system = classify_prompt(insurers, products, stories)
    insurer_owner = lambda item: item.get("source_owner") in insurers
    ctx = (insurers, {p["id"] for p in products}, {s["id"] for s in stories}, insurer_owner)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    done = failed = 0

    def run(batch):
        payload = [{"id": i["id"], "owner": i.get("source_owner", ""), "source_kind": i.get("source_kind", ""),
                    "date": i["date"], "title": i.get("title_ja") or i.get("title_en", "")} for i in batch]
        by_id = {i["id"]: i for i in batch}
        last = None
        for _attempt in range(2):
            try:
                reply = parse_array(ask(binary, system, json.dumps(payload, ensure_ascii=False), tempfile.gettempdir()))
                changes = {}
                for r in reply:
                    item = by_id.get(r.get("id")) if isinstance(r, dict) else None
                    if item:
                        label = clean_label(r, item, ctx)
                        label["enriched"] = {"by": MODEL, "date": common.today()}
                        changes[item["id"]] = label
                if len(changes) < len(batch) * 0.9:
                    raise ValueError("reply covered %d of %d items" % (len(changes), len(batch)))
                patch_items(changes)
                return len(changes), len(batch) - len(changes)
            except Unavailable:
                raise
            except Exception as err:
                last = err
        log("  a batch of %d could not be labelled: %s" % (len(batch), str(last)[:200]))
        return 0, len(batch)

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for n, (ok, bad) in enumerate(pool.map(run, batches), 1):
            done += ok
            failed += bad
            log("  labelled %d of %d" % (done, len(todo)))
    return done, failed


def release_text(item, folder):
    """Fetch the release. Returns (prompt, needs_read). Text for HTML, a saved file for PDF."""
    body = common.fetch(item["url"], retries=2, timeout=60, pause=0.5)
    if body[:5] == b"%PDF-":
        path = Path(folder) / "release.pdf"
        path.write_bytes(body)
        return "Headline: %s\nDate: %s\nThe announcement is the PDF file %s. Read it with the Read tool, then reply." % (
            item.get("title_ja") or item.get("title_en", ""), item["date"], path), True
    page = common.decode(body)
    page = re.sub(r"<(script|style|nav|header|footer)\b[\s\S]*?</\1>", " ", page, flags=re.I)
    text = common.clean(page)
    if len(text) < 80:
        return None, False
    return "Headline: %s\nDate: %s\n\nText of the page (site navigation may be mixed in):\n%s" % (
        item.get("title_ja") or item.get("title_en", ""), item["date"], text[:9000]), False


def summarise(binary, todo, jobs, log):
    done = failed = 0

    def run(item):
        folder = tempfile.mkdtemp(prefix="news-")
        try:
            prompt, needs_read = release_text(item, folder)
            if not prompt:
                return item, None, "the page had no text"
            text = ask(binary, SUMMARISE, prompt, folder, read_files=needs_read).strip()
            if not text or text.upper().startswith("UNREADABLE"):
                return item, None, "could not be read"
            return item, text[:SUMMARY_CHARS + 100], None
        except Unavailable:
            raise
        except Exception as err:
            return item, False, str(err)[:160]  # a fetch or model error: try again on a later run
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for item, text, error in pool.map(run, todo):
            if text:
                patch_items({item["id"]: {"summary_en": text}})
                done += 1
            else:
                if text is None:
                    # Unreadable: remember it so the same release is not fetched on every run.
                    patch_items({item["id"]: {"summary_failed": common.today()}})
                failed += 1
                log("  no summary for %s: %s" % (item["id"], error))
            if (done + failed) % 10 == 0:
                log("  summarised %d of %d" % (done, len(todo)))
    return done, failed


def run(only=None, limit=None, summaries=True, redo=False, jobs=4, log=print):
    """Returns True when everything asked for was done."""
    news.status("enrich", "running")
    try:
        binary = find_claude()
        items = [i for i in news.all_items() if not only or i.get("source_owner") in only]
        todo = [i for i in items if redo or not i.get("category")]
        todo.sort(key=lambda i: i["date"], reverse=True)
        todo = todo[:limit] if limit else todo
        log("\n=== English titles and categories ===")
        log("  %d items to label" % len(todo))
        ok1 = bad1 = ok2 = bad2 = 0
        if todo:
            ok1, bad1 = classify(binary, todo, load_context(), jobs, log)
        log("  %d new labels%s" % (ok1, (", %d not labelled" % bad1) if bad1 else ""))
        if summaries:
            items = [i for i in news.all_items() if not only or i.get("source_owner") in only]
            want = [i for i in items if i.get("importance") == "high" and i.get("relevant") is not False
                    and not i.get("summary_en") and not i.get("summary_failed")]
            want.sort(key=lambda i: i["date"], reverse=True)
            want = want[:limit] if limit else want
            log("\n=== Summaries of high-importance items ===")
            log("  %d items to summarise" % len(want))
            if want:
                ok2, bad2 = summarise(binary, want, min(jobs, 3), log)
            log("  %d new summaries%s" % (ok2, (", %d not summarised" % bad2) if bad2 else ""))
        if bad1:
            news.status("enrich", "failed", "%d items could not be labelled (see the log)" % bad1)
            return False
        news.status("enrich", "ok", ("%d releases could not be summarised" % bad2) if bad2 else "")
        return True
    except Unavailable as err:
        news.status("enrich", "failed", str(err))
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", help="comma-separated owner ids (the items file names)")
    parser.add_argument("--limit", type=int, help="at most this many items per pass")
    parser.add_argument("--no-summaries", action="store_true", help="titles and categories only")
    parser.add_argument("--redo", action="store_true", help="label items again even if they have a category")
    parser.add_argument("--jobs", type=int, default=4, help="calls at once (default 4)")
    args = parser.parse_args()
    ok = run(only=set(args.only.split(",")) if args.only else None, limit=args.limit,
             summaries=not args.no_summaries, redo=args.redo, jobs=args.jobs)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
