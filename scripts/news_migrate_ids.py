"""Move the news tracker onto the rate-side insurer ids, so each company has one id.

The news tracker first added its own records for six insurers that the rate collectors already had under short
ids. The short ids are canonical (products and rate files are keyed on them). This script:

  1. merges data/insurers/<old>.json into data/insurers/<new>.json (every field kept; where both have a field
     with different values the rate-side value is kept and the other is recorded under "notes", except
     access_note, where the two texts are joined) and deletes <old>.json;
  2. rewrites every news reference: item ids, insurer_ids, source_owner and snapshot paths in
     data/news/items/*.jsonl, the item file names, data/news/state.json keys, insurer_ids in
     data/news/stories/*.json and data/news/sources/*.json;
  3. moves the snapshot folders sources/<old>/news/ to sources/<new>/news/ and the site readers
     scripts/news_sites/<old>.py to <new>.py (with `git mv` inside a git checkout), and points the
     "parser" entries at the new module names.

It is re-runnable: anything already migrated is left alone, and a second run reports no changes.

Usage:
    python3 scripts/news_migrate_ids.py            # migrate
    python3 scripts/news_migrate_ids.py --dry-run  # say what would change
"""

import argparse
import json
import shutil
import subprocess
import sys

import common
import news

# news tracker id -> rate-side id
MAP = {
    "creditagricole": "cal",
    "metlife": "met",
    "manulife": "mnl",
    "meijiyasuda": "mys",
    "nippon": "nli",
    "orix": "orx",
}

INSURERS = common.DATA / "insurers"
SITES = common.ROOT / "scripts" / "news_sites"

# Key order for a merged record (same layout as pgf.json); keys not listed keep their place at the end.
ORDER = ("id", "name_ja", "name_en", "name_short_en", "news_tier", "group", "site", "rate_hub_url", "rate_update",
         "collector", "notes", "rate_period", "news_sources", "access", "access_note")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, data, dry):
    if not dry:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def in_git():
    return subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=common.ROOT,
                          capture_output=True).returncode == 0


def tracked(path):
    return subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], cwd=common.ROOT,
                          capture_output=True).returncode == 0


def move(src, dst, dry, git):
    if dry:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if git and tracked(src):
        subprocess.run(["git", "mv", str(src), str(dst)], cwd=common.ROOT, check=True)
    else:
        shutil.move(str(src), str(dst))


def remove(path, dry, git):
    if dry:
        return
    if git and tracked(path):
        subprocess.run(["git", "rm", "-q", str(path)], cwd=common.ROOT, check=True)
    else:
        path.unlink()


def new_id(i):
    return MAP.get(i, i)


def map_ids(ids):
    out = []
    for i in ids:
        i = new_id(i)
        if i not in out:
            out.append(i)
    return out


def map_parser(spec):
    if isinstance(spec, str) and ":" in spec:
        mod, fn = spec.split(":", 1)
        return "%s:%s" % (new_id(mod), fn)
    return spec


def map_sources(sources):
    out = []
    for s in sources or []:
        s = dict(s)
        if "parser" in s:
            s["parser"] = map_parser(s["parser"])
        out.append(s)
    return out


# ------------------------------------------------------------------------------------------- insurers
def merge_insurer(old, new, log, dry, git):
    old_path, new_path = INSURERS / (old + ".json"), INSURERS / (new + ".json")
    if not old_path.exists():
        return
    if not new_path.exists():
        sys.exit("%s exists but %s does not" % (common.rel(old_path), common.rel(new_path)))
    a, b = load(new_path), load(old_path)  # a: rate side (kept), b: news side
    if "news_sources" in b:
        b["news_sources"] = map_sources(b["news_sources"])
    merged, notes = dict(a), list(a.get("notes") or [])
    for k, v in b.items():
        if k == "id":
            continue
        if k not in merged:
            merged[k] = v
        elif merged[k] == v:
            continue
        elif k == "access_note":
            merged[k] = "Rates: %s News lists: %s" % (merged[k], v)
            log.append("  %s.access_note: joined the rate-side and news-side notes" % new)
        elif k == "notes":
            notes += [n for n in v if n not in notes]
        else:
            notes.append("The news tracker's record gave %s as %s." % (k, json.dumps(v, ensure_ascii=False)))
            log.append("  %s.%s: kept %s, noted %s" % (new, k, json.dumps(merged[k], ensure_ascii=False),
                                                      json.dumps(v, ensure_ascii=False)))
    if notes:
        merged["notes"] = notes
    merged = {k: merged[k] for k in ORDER if k in merged} | {k: v for k, v in merged.items() if k not in ORDER}
    log.append("insurer: merged %s into %s, deleted %s" % (common.rel(old_path), common.rel(new_path),
                                                           common.rel(old_path)))
    dump(new_path, merged, dry)
    remove(old_path, dry, git)


def fix_config(log, dry):
    """Parser names and insurer_ids in every insurer and extra-source file."""
    for path in sorted(INSURERS.glob("*.json")) + sorted(news.EXTRA_SOURCES.glob("*.json")):
        data = load(path)
        changed = False
        if data.get("news_sources"):
            fixed = map_sources(data["news_sources"])
            if fixed != data["news_sources"]:
                data["news_sources"], changed = fixed, True
        if data.get("insurer_ids"):
            fixed = map_ids(data["insurer_ids"])
            if fixed != data["insurer_ids"]:
                data["insurer_ids"], changed = fixed, True
        if changed:
            log.append("config: %s" % common.rel(path))
            dump(path, data, dry)


# ---------------------------------------------------------------------------------------------- items
def fix_item(item):
    item = dict(item)
    if item.get("source_owner") in MAP:
        item["source_owner"] = MAP[item["source_owner"]]
    iid = item.get("id", "")
    for old, new in MAP.items():
        if iid.startswith(old + "-"):
            item["id"] = new + iid[len(old):]
            break
    if isinstance(item.get("insurer_ids"), list):
        item["insurer_ids"] = map_ids(item["insurer_ids"])
    snap = item.get("snapshot")
    if isinstance(snap, str):
        for old, new in MAP.items():
            if snap.startswith("sources/%s/" % old):
                item["snapshot"] = "sources/%s/%s" % (new, snap[len("sources/%s/" % old):])
                break
    return item


def migrate_items(log, counts, dry, git):
    for path in sorted(news.ITEMS.glob("*.jsonl")):
        items = news.read_items(path)
        fixed = [fix_item(i) for i in items]
        changed = sum(1 for a, b in zip(items, fixed) if a != b)
        for a, b in zip(items, fixed):
            for k in ("id", "insurer_ids", "source_owner", "snapshot"):
                if a.get(k) != b.get(k):
                    counts[k] = counts.get(k, 0) + 1
        if path.stem in MAP:
            target = news.ITEMS / (MAP[path.stem] + ".jsonl")
            if target.exists():
                have = news.read_items(target)
                ids = {i["id"] for i in have}
                log.append("items: merged %s into %s" % (common.rel(path), common.rel(target)))
                if not dry:
                    news.write_items(target, have + [i for i in fixed if i["id"] not in ids])
                remove(path, dry, git)
                continue
            log.append("items: %s -> %s (%d items rewritten)" % (common.rel(path), common.rel(target), changed))
            move(path, target, dry, git)
            if not dry:
                news.write_items(target, fixed)
        elif changed:
            log.append("items: %s (%d items rewritten)" % (common.rel(path), changed))
            if not dry:
                news.write_items(path, fixed)


def migrate_state(log, dry):
    state = news.read_state()
    src = state.get("sources", {})
    fixed = {}
    for key, rec in src.items():
        owner, sep, rest = key.partition("/")
        fixed[new_id(owner) + sep + rest] = rec
    if fixed != src:
        log.append("state: %d source keys renamed" % sum(1 for k in src if k.partition("/")[0] in MAP))
        state["sources"] = fixed
        if not dry:
            news.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                  encoding="utf-8")


def migrate_stories(log, dry):
    for path in sorted((news.NEWS / "stories").glob("*.json")):
        data = load(path)
        fixed = map_ids(data.get("insurer_ids") or [])
        if fixed != (data.get("insurer_ids") or []):
            data["insurer_ids"] = fixed
            log.append("story: %s" % common.rel(path))
            dump(path, data, dry)


# ------------------------------------------------------------------------------------ folders, modules
def migrate_folders(log, dry, git):
    for old, new in MAP.items():
        src = common.SOURCES / old / "news"
        if src.exists():
            dst = common.SOURCES / new / "news"
            files = sorted(p for p in src.rglob("*") if p.is_file())
            for f in files:
                target = dst / f.relative_to(src)
                if target.exists():
                    sys.exit("would overwrite %s" % common.rel(target))
                move(f, target, dry, git)
            log.append("snapshots: %s -> %s (%d files)" % (common.rel(src), common.rel(dst), len(files)))
        top = common.SOURCES / old
        if not dry and top.exists():
            for d in sorted(top.rglob("*"), reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            if not any(top.iterdir()):
                top.rmdir()
        mod = SITES / (old + ".py")
        if mod.exists():
            target = SITES / (new + ".py")
            if target.exists():
                sys.exit("would overwrite %s" % common.rel(target))
            log.append("reader: %s -> %s" % (common.rel(mod), common.rel(target)))
            move(mod, target, dry, git)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    args = parser.parse_args()
    dry, git = args.dry_run, in_git()
    log, counts = [], {}
    for old, new in MAP.items():
        merge_insurer(old, new, log, dry, git)
    fix_config(log, dry)
    migrate_items(log, counts, dry, git)
    migrate_state(log, dry)
    migrate_stories(log, dry)
    migrate_folders(log, dry, git)
    print("\n".join(log) or "nothing to migrate")
    if counts:
        print("item fields rewritten: " + ", ".join("%s %d" % kv for kv in sorted(counts.items())))


if __name__ == "__main__":
    main()
