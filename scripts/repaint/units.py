"""Check a translator's units.json against the page it translates.

    .venv/bin/python scripts/repaint/units.py PAGE_DIR

Prints every problem found (exit code 1), or "units.json is valid" (exit code 0).

From Python:
    load_units(page_dir) -> dict          the units, or ValueError listing what to fix
    validate(units, page) -> list[str]    every problem, for callers that want them all

The rules are the ones in translator_prompt.md: every line of page.json is used exactly once
(in a unit, in `keep` or in `drop`); each unit has one source; `en` is English only; every
`style` names a real line or "line:span"; `en_short` has the same shape as `en`.
"""
import json
import re
import sys
from pathlib import Path

ROLES = ("heading", "subheading", "body", "bullet", "label", "table_header", "table_cell",
         "warning", "footnote", "caption", "speech")
SOURCES = ("lines", "outlined", "region")
UNIT_KEYS = {"id", "role", "ja", "en", "en_short", "origin"} | set(SOURCES)
ORIGINS = ("model", "rule", "memory")
SEGMENT_KEYS = {"t", "style", "br"}
TOP_KEYS = {"units", "keep", "drop", "notes", "page_hash"}
# Hiragana, katakana (but not the middle dot ・, which also serves as a bullet), CJK ideographs,
# compatibility ideographs and half-width katakana.
JAPANESE = re.compile(r"[぀-ゟ゠-ヺー-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")
REGION_TOLERANCE = 2.0   # points a measured region may stick out past the page edge
MAX_LISTED = 25          # ids listed in one message before "and N more"


def _ids(ids):
    ids = sorted(ids, key=lambda v: (str(type(v)), v))
    s = ", ".join(str(i) for i in ids[:MAX_LISTED])
    return s + (" and %d more" % (len(ids) - MAX_LISTED) if len(ids) > MAX_LISTED else "")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (isinstance(v, (int, float))) and not isinstance(v, bool)


def _check_style(style, lines_by_id, where):
    """Return a problem string, or None if `style` names a real line or line:span."""
    if _is_int(style):
        return None if style in lines_by_id else "%s: style %d is not a line id in page.json." % (where, style)
    if isinstance(style, str):
        m = re.fullmatch(r"\s*(\d+)\s*(?::\s*(\d+)\s*)?", style)
        if not m:
            return ('%s: style "%s" is not a line id or "line:span" (for example 12 or "12:1").' % (where, style))
        line, span = int(m.group(1)), m.group(2)
        if line not in lines_by_id:
            return '%s: style "%s" names line %d, which is not in page.json.' % (where, style, line)
        if span is not None:
            n = len(lines_by_id[line].get("spans") or [])
            if int(span) >= n:
                return ('%s: style "%s" names span %s of line %d, but that line has %d span%s (numbered from 0).'
                        % (where, style, span, line, n, "" if n == 1 else "s"))
        return None
    return "%s: style must be a line id or \"line:span\", not %s." % (where, json.dumps(style, ensure_ascii=False))


def _norm_style(style):
    if isinstance(style, str):
        m = re.fullmatch(r"\s*(\d+)\s*(?::\s*(\d+)\s*)?", style)
        if m:
            return (int(m.group(1)), int(m.group(2)) if m.group(2) is not None else None)
    if _is_int(style):
        return (style, None)
    return style


def _check_segments(segs, field, uid, lines_by_id, problems, style_optional=False):
    """Check one `en` / `en_short` list. Returns True if its shape could be read."""
    where = "Unit %s %s" % (uid, field)
    if not isinstance(segs, list) or not segs:
        problems.append("%s must be a non-empty list of segments like {\"t\": \"...\", \"style\": 12}." % where)
        return False
    has_text = False
    for i, s in enumerate(segs):
        w = "%s segment %d" % (where, i + 1)
        if not isinstance(s, dict):
            problems.append("%s must be an object with \"t\" and \"style\", not %s." % (w, json.dumps(s, ensure_ascii=False)[:60]))
            continue
        extra = set(s) - SEGMENT_KEYS
        if extra:
            problems.append("%s has keys the program does not use: %s. A segment has only \"t\", \"style\" and \"br\"; "
                            "the program decides fonts, sizes and positions." % (w, ", ".join(sorted(extra))))
        if "br" in s and not isinstance(s["br"], bool):
            problems.append("%s: \"br\" must be true or false." % w)
        t = s.get("t")
        if t is None or not isinstance(t, str):
            problems.append("%s has no text (\"t\" must be a string)." % w)
            continue
        if not t.strip():
            if s.get("br") is True:
                continue   # a segment that is only a line break
            problems.append("%s has empty text. Remove it, or make it a break with \"br\": true." % w)
            continue
        has_text = True
        if "style" not in s:
            if style_optional:
                continue
            problems.append("%s has no \"style\" (the id of the line whose look it takes)." % w)
        else:
            p = _check_style(s["style"], lines_by_id, w)
            if p:
                problems.append(p)
        jp = JAPANESE.findall(t)
        if jp:
            problems.append("%s still contains Japanese (%s) in \"%s\". Translate it; Japanese belongs only in \"ja\"."
                            % (w, "".join(dict.fromkeys(jp))[:12], t[:60]))
    if not has_text:
        problems.append("%s has no English text." % where)
    return True


def _shape(segs):
    return [(_norm_style(s.get("style")) if (s.get("t") or "").strip() else None, bool(s.get("br")))
            for s in segs if isinstance(s, dict)]


def validate(units, page, model_part=False):
    """Every problem in `units` (the parsed units.json) against `page` (the parsed page.json), as plain sentences.

    model_part=False (a finished units.json): every line is used exactly once.
    model_part=True (what the model wrote for a page whose page.json has lines marked "done"): the done lines
    must not appear at all, and every other line exactly once."""
    problems = []
    if not isinstance(page, dict) or not isinstance(page.get("lines"), list):
        return ["page.json has no list of lines; run extract.py again for this page."]
    lines_by_id = {l["id"]: l for l in page["lines"] if isinstance(l, dict) and "id" in l}
    done_ids = {lid for lid, l in lines_by_id.items() if l.get("done")} if model_part else set()
    outlined_ids = {o["id"] for o in page.get("outlined") or [] if isinstance(o, dict) and "id" in o}
    W, H = page.get("width"), page.get("height")

    if not isinstance(units, dict):
        return ["units.json must be one object with \"units\", \"keep\", \"drop\" and \"notes\"."]
    extra = set(units) - TOP_KEYS
    if extra:
        problems.append("units.json has top-level keys the program does not use: %s (allowed: units, keep, drop, notes)."
                        % ", ".join(sorted(extra)))
    ulist = units.get("units")
    if not isinstance(ulist, list) or not ulist:
        problems.append("units.json has no \"units\" list, or it is empty.")
        ulist = []
    for key in ("keep", "drop"):
        v = units.get(key, [])
        if not isinstance(v, list) or not all(_is_int(x) for x in v):
            problems.append("\"%s\" must be a list of line ids (whole numbers)." % key)
    notes = units.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        problems.append("\"notes\" must be a list of sentences (strings).")

    used = {}   # line id -> list of places it is used

    def use(lid, place):
        used.setdefault(lid, []).append(place)

    seen_uids = set()
    for k, u in enumerate(ulist):
        if not isinstance(u, dict):
            problems.append("Unit number %d is not an object." % (k + 1))
            continue
        uid = u.get("id")
        if not isinstance(uid, str) or not uid.strip():
            problems.append("Unit number %d has no \"id\" (use u1, u2, ...)." % (k + 1))
            uid = "#%d" % (k + 1)
        elif uid in seen_uids:
            problems.append("Unit id %s is used for more than one unit; give each unit its own id." % uid)
        seen_uids.add(uid)
        extra = set(u) - UNIT_KEYS
        if extra:
            problems.append("Unit %s has keys the program does not use: %s. Do not give layout instructions; "
                            "put anything a reviewer should know in \"notes\"." % (uid, ", ".join(sorted(extra))))

        srcs = [s for s in SOURCES if s in u]
        if len(srcs) != 1:
            problems.append("Unit %s must have exactly one source: \"lines\", \"outlined\" or \"region\" (it has %s)."
                            % (uid, " and ".join(srcs) if srcs else "none"))
        if "lines" in u:
            ls = u["lines"]
            if not isinstance(ls, list) or not ls or not all(_is_int(x) for x in ls):
                problems.append("Unit %s: \"lines\" must be a non-empty list of line ids." % uid)
            else:
                bad = [x for x in ls if x not in lines_by_id]
                if bad:
                    problems.append("Unit %s uses line id%s %s, which page.json does not have."
                                    % (uid, "s" if len(bad) > 1 else "", _ids(bad)))
                for x in ls:
                    use(x, "unit " + uid)
        if "outlined" in u:
            o = u["outlined"]
            olist = o if isinstance(o, list) else [o]
            if not olist or not all(isinstance(x, str) for x in olist):
                problems.append("Unit %s: \"outlined\" must be an id from page.json's outlined list, such as \"o0\"." % uid)
            else:
                bad = [x for x in olist if x not in outlined_ids]
                if bad:
                    problems.append("Unit %s: outlined id %s is not in page.json (it has: %s)."
                                    % (uid, ", ".join(bad), ", ".join(sorted(outlined_ids)) or "none"))
        if "region" in u:
            r = u["region"]
            if not isinstance(r, list) or len(r) != 4 or not all(_is_num(v) for v in r):
                problems.append("Unit %s: \"region\" must be four numbers [x0, y0, x1, y1] in points." % uid)
            else:
                x0, y0, x1, y1 = r
                if x1 - x0 < 2 or y1 - y0 < 2:
                    problems.append("Unit %s: region %s is empty or upside down (x1 must be greater than x0, y1 greater than y0)."
                                    % (uid, r))
                elif _is_num(W) and _is_num(H) and (x0 < -REGION_TOLERANCE or y0 < -REGION_TOLERANCE
                                                    or x1 > W + REGION_TOLERANCE or y1 > H + REGION_TOLERANCE):
                    problems.append("Unit %s: region %s goes outside the page (%.0f x %.0f points). Measure it in points, "
                                    "not image pixels: page.png is 150 dpi, so divide pixels by 150/72 = 2.083."
                                    % (uid, r, W, H))

        if "origin" in u and u["origin"] not in ORIGINS:
            problems.append("Unit %s: origin %s is not one of: %s." % (uid, json.dumps(u["origin"], ensure_ascii=False), ", ".join(ORIGINS)))
        role = u.get("role")
        if role not in ROLES:
            problems.append("Unit %s: role %s is not one of: %s." % (uid, json.dumps(role, ensure_ascii=False), ", ".join(ROLES)))
        picture = "lines" not in u and ("outlined" in u or "region" in u)
        ja = u.get("ja")
        if picture and (not isinstance(ja, str) or not ja.strip()):
            problems.append("Unit %s has no \"ja\": for text read from the image (outlined or region), give the Japanese you read." % uid)
        elif "ja" in u and not isinstance(ja, str):
            problems.append("Unit %s: \"ja\" must be text." % uid)

        ok_en = _check_segments(u.get("en"), "en", uid, lines_by_id, problems, style_optional=picture)
        if "en_short" in u:
            ok_short = _check_segments(u["en_short"], "en_short", uid, lines_by_id, problems, style_optional=picture)
            if ok_en and ok_short and _shape(u["en"]) != _shape(u["en_short"]):
                problems.append("Unit %s: en_short must have the same segments as en (same number, same styles, breaks in the "
                                "same places), only shorter text. Fix it, or leave en_short out." % uid)

    keep = units.get("keep", []) if isinstance(units.get("keep", []), list) else []
    drop = units.get("drop", []) if isinstance(units.get("drop", []), list) else []
    for key, lst in (("keep", keep), ("drop", drop)):
        bad = [x for x in lst if _is_int(x) and x not in lines_by_id]
        if bad:
            problems.append("\"%s\" lists line id%s %s, which page.json does not have." % (key, "s" if len(bad) > 1 else "", _ids(bad)))
        for x in lst:
            if _is_int(x):
                use(x, key)

    twice = {lid: places for lid, places in used.items() if len(places) > 1 and lid in lines_by_id}
    for lid in sorted(twice)[:MAX_LISTED]:
        problems.append("Line %d is used more than once (in %s); use it in exactly one place." % (lid, " and ".join(twice[lid])))
    if len(twice) > MAX_LISTED:
        problems.append("... and %d more lines are used more than once." % (len(twice) - MAX_LISTED))
    if done_ids:
        again = sorted(lid for lid in used if lid in done_ids)
        if again:
            problems.append("Line%s %s %s marked \"done\" in page.json (already translated); leave %s out of units, keep and drop."
                            % ("s" if len(again) > 1 else "", _ids(again), "are" if len(again) > 1 else "is",
                               "them" if len(again) > 1 else "it"))
    missing = [lid for lid in lines_by_id if lid not in used and lid not in done_ids]
    if missing:
        problems.append("Line%s %s %s not used anywhere: put each in a unit's \"lines\", in \"keep\" (nothing to translate) or "
                        "in \"drop\" (absorbed into another unit)." % ("s" if len(missing) > 1 else "", _ids(missing),
                                                                      "are" if len(missing) > 1 else "is"))
    return problems


def _read_json(path, what):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ValueError("%s was not found at %s." % (what, path))
    except OSError as e:
        raise ValueError("%s could not be read (%s)." % (what, e))
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("%s is not valid JSON: %s at line %d, column %d." % (what, e.msg, e.lineno, e.colno))


def load_units(page_dir, model_part=False):
    """Load PAGE_DIR/units.json, check it against PAGE_DIR/page.json and return it; ValueError lists what to fix."""
    page_dir = Path(page_dir)
    page = _read_json(page_dir / "page.json", "page.json")
    units = _read_json(page_dir / "units.json", "units.json")
    problems = validate(units, page, model_part)
    if problems:
        raise ValueError(format_problems(problems, page_dir / "units.json"))
    return units


def format_problems(problems, path=None):
    head = "%s has %d problem%s:" % (path or "units.json", len(problems), "" if len(problems) == 1 else "s")
    return "\n".join([head] + ["- " + p for p in problems])


def main(argv):
    if len(argv) != 2:
        print("usage: units.py PAGE_DIR", file=sys.stderr)
        return 2
    page_dir = Path(argv[1])
    try:
        page = _read_json(page_dir / "page.json", "page.json")
        units = _read_json(page_dir / "units.json", "units.json")
    except ValueError as e:
        print(e)
        return 1
    problems = validate(units, page)
    if problems:
        print(format_problems(problems, page_dir / "units.json"))
        return 1
    print("units.json is valid: %d units, %d kept, %d dropped." % (len(units["units"]), len(units.get("keep", [])), len(units.get("drop", []))))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    sys.exit(main(sys.argv))
