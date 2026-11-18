"""Self-tests for checks.py. Prints PASS/FAIL per case and exits 1 if any case fails.

    .venv/bin/python scripts/repaint/checks_selftest.py [--keep DIR]

Cases
1. Trial regression: the earlier trial's English page (reports/translation-trial/page4_english_v2.pdf,
   no placements.json) must show the defects known to be on it.
2. Original-as-output: the ink-vs-graphics logic run on the original Japanese of the three test
   pages must report no collisions, overflows or margin overruns.
3. Synthetic defects: a clean English page is built from dfl-pcu3-p04 (Japanese removed, English
   set inside each original text area), then single defects are injected one at a time and the
   matching check must fire; the clean page must not fire them.
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import checks                                    # noqa: E402
import checks_text as T                          # noqa: E402
import extract                                   # noqa: E402

TRIAL = ROOT / "reports" / "translation-trial"
P04 = ROOT / "translations" / "_work" / "dfl-pcu3-p04"
PAGES = [ROOT / "translations" / "_work" / d for d in ("dfl-pcu3-p04", "dfl-pcu3-p09", "tdf-ftw4-p04", "claude-dfl-pcu3-p06", "claude-sml-tg3-p03")]
PAGES = [d for d in PAGES if (d / "units.json").exists()]

results = []


def case(name, ok, detail=""):
    results.append((name, ok))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail else ""))


def findings(res, uid=None):
    if uid is None:
        return res["page"]
    return next((u["findings"] for u in res["units"] if u["id"] == uid), [])


def has(res, uid, check, sev=None):
    return any(f["check"] == check and (sev is None or f["severity"] == sev) for f in findings(res, uid))


# ---------------------------------------------------------------- 1. trial regression

SPLITS = {   # units whose segments carry different styles: split the trial's English the same way
    "u3": [("Death and surrender benefits are limited:", 109), ("for those who want to receive more at maturity", 108)],
    "u11": [("●", "92:0"), (None, "92:1")],
    "u12": [("●", "94:0"), (None, "94:1")],
    "u16": [("How it works", "97:0"), ("(illustration)", "97:1")],
    "u32": [("Single premium", "11:8", True), ("(basic sum insured)", "11:1")],
    "u34": [("In chosen", 6, True), ("currency", 7)],
    "u45": [("In chosen", 8, True), ("currency", 9)],
    "u35": [("Account value", 12, True), ("(in chosen currency)", 13)],
    "u37": [("Death benefit", 17, True), ("(in chosen currency)", 18)],
    "u39": [("Surrender value", 14, True), ("(in chosen currency)", 15)],
    "u36": [("During the accumulation period,", 23, True), ("death and surrender benefits are limited…", 24)],
    "u42": [("Receive more ", 21), ("at maturity", 20)],
}


def build_trial_fixture(d):
    """A page folder for the trial output: page.json and units.json (roles, lines, styles) of
    dfl-pcu3-p04, whose line ids match the trial's, with the English the trial actually used."""
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(P04 / "page.json", d / "page.json")
    shutil.copy(TRIAL / "page4_english_v2.pdf", d / "english.pdf")
    real = json.loads((P04 / "units.json").read_text(encoding="utf-8"))
    trial = json.loads((TRIAL / "page4_translation_v2.json").read_text(encoding="utf-8"))
    units = []
    for u in real["units"]:
        if u.get("lines"):
            ents = [e for e in trial if set(e.get("ids") or []) & set(u["lines"])]
        else:
            ents = [e for e in trial if "outline" in e] if u.get("outlined") == "o0" else []
        text = " ".join(re.sub(r"<[^>]+>", "", e["en"]) for e in ents if e.get("en")).strip()
        if not text and u.get("lines"):          # the trial converted these mechanically
            ja = u.get("ja", "")
            m = re.fullmatch(r"(\d+)\s*年", ja) or None
            m2 = re.fullmatch(r"(\d+)～(\d+)歳", ja)
            text = ("%s yrs" % m.group(1)) if m else ("%s–%s" % m2.groups() if m2 else "")
        if not text:
            continue
        nu = {k: u[k] for k in ("id", "role", "lines", "outlined", "region", "ja") if k in u}
        if u["id"] in SPLITS:
            segs, rest = [], text
            for sp in SPLITS[u["id"]]:
                t = sp[0] if sp[0] is not None else rest
                rest = rest.replace(t, "", 1).strip()
                seg = {"t": t, "style": sp[1]}
                if len(sp) > 2:
                    seg["br"] = True
                segs.append(seg)
            nu["en"] = segs
        else:
            nu["en"] = [{"t": text, "style": u["en"][0]["style"]}]
        units.append(nu)
    (d / "units.json").write_text(json.dumps({"units": units, "keep": real["keep"], "drop": real["drop"]},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def test_trial(tmp):
    d = build_trial_fixture(tmp / "trial")
    res = checks.check_page(d)
    print("      trial: %s, %d errors, %d warnings, %.1fs" % (res["summary"]["verdict"], res["summary"]["errors"],
                                                             res["summary"]["warnings"], res["summary"]["seconds"]))
    case("trial: pill overflow on 'Account value (in chosen currency)' (u35)", has(res, "u35", "container", "error") or has(res, "u35", "collision", "error"))
    case("trial: rotated 'Single premium (basic sum insured)' hits the brace (u32)",
         has(res, "u32", "collision", "error") or has(res, "u32", "vertical_extent", "error") or has(res, "u32", "container", "error"))
    case("trial: red risk warning shrunk 7.8 -> 6.8pt (u71)", has(res, "u71", "size", "error"))
    case("trial: 'How it works' heading smaller than its bullets (u16)", has(res, "u16", "hierarchy", "error"))
    case("trial: headline past the right margin (u3)", has(res, "u3", "margins", "error"))
    case("trial: diagram disclaimer past the right margin (u51)", has(res, "u51", "margins", "error"))
    case("trial: two-size emphasis flattened (u42)", has(res, "u42", "emphasis_size"))
    case("trial: sibling table headers at different sizes (u20/u21)", has(res, "u20", "siblings") or has(res, "u21", "siblings"))
    case("trial: lost hanging indent (u11)", has(res, "u11", "hanging_indent"))
    case("trial: Japanese punctuation in kept badges", any(f["check"] == "kept_punctuation" for f in res["page"]))
    case("trial: line break inside '(in chosen currency)' (u35)", has(res, "u35", "bad_break") or has(res, "u35", "line_breaks"))
    return res


# ---------------------------------------------------------------- 2. original as output

def test_original():
    for d in PAGES:
        F, content = checks.ink_on_original(d)
        errs = [(uid, f) for uid, fs in F.units.items() for f in fs if f["severity"] == "error"]
        detail = "; ".join("%s %s %s" % (uid, f["check"], f["message"][:60]) for uid, f in errs[:6])
        case("original-as-output has no ink errors: %s" % d.name, not errs, detail)


# ---------------------------------------------------------------- 3. synthetic defects

def _font_css():
    return fitz.css_for_pymupdf_font("notos", archive=_ARCH, name="en")


_ARCH = fitz.Archive()


def _html(u, by_id, size_scale=1.0, color=None):
    parts = []
    for seg in u["en"]:
        st = T.style_of(seg["style"], by_id)
        parts.append('<span style="font-size:%.2fpt;color:#%06x;font-weight:%s">%s</span>%s' % (
            st["size"] * size_scale, st["color"] if color is None else color, "bold" if st["weight"] >= 700 else "normal",
            seg["t"].replace("&", "&amp;").replace("<", "&lt;"), "<br>" if seg.get("br") else ""))
    return "".join(parts)


def _unit_rect(u, by_id):
    # the Japanese line box includes the full-width side bearings of its first and last characters,
    # so the English gets a slightly narrower box
    ls = [by_id[l] for l in u["lines"]]
    return fitz.Rect(min(l["bbox"][0] for l in ls) + 1.5, min(l["bbox"][1] for l in ls) - 1,
                     max(l["bbox"][2] for l in ls) - 1.5, max(l["bbox"][3] for l in ls) + 1)


def _css(u):
    return _font_css() + "* {font-family: en; line-height: 1.12; margin:0; padding:0; text-align: %s}" % (
        "center" if u["role"] in ("label", "table_header", "caption", "table_cell") else "left")


def build_clean(d, mutate=None, region=False):
    """A minimal correct layout of dfl-pcu3-p04. Only units whose English fits inside the original
    text's box at the ORIGINAL size are translated; the rest stay in Japanese and are listed as kept,
    so the page is clean by construction. `mutate(uid, rect, scale, color)` returns a changed
    (rect, scale, color) to inject one defect. With region=True a unit whose source is a `region`
    (Japanese inside a picture) is added, its English set on the picture."""
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(P04 / "page.json", d / "page.json")
    pj = json.loads((P04 / "page.json").read_text(encoding="utf-8"))
    uj = json.loads((P04 / "units.json").read_text(encoding="utf-8"))
    by_id = {l["id"]: l for l in pj["lines"]}
    src = pj["source"] if Path(pj["source"]).is_absolute() else str(ROOT / pj["source"])
    tmp = fitz.open()
    tp = tmp.new_page(width=pj["width"], height=pj["height"])
    units = []
    for u in uj["units"]:
        if not u.get("lines") or any(by_id[l]["vert"] for l in u["lines"]):
            continue
        spare, _ = tp.insert_htmlbox(_unit_rect(u, by_id), _html(u, by_id), css=_css(u), archive=_ARCH, scale_low=1)
        if spare >= 0:
            units.append(u)
            continue
        # does not fit: try a shortened wording (first words only) in the first segment's style
        ws = " ".join(seg["t"] for seg in u["en"]).split()
        for k in range(len(ws) - 1, 1, -1):
            v = dict(u, en=[{"t": " ".join(ws[:k]), "style": u["en"][-1]["style"] if u["role"] == "warning" else u["en"][0]["style"]}])
            v.pop("en_short", None)
            spare, _ = tp.insert_htmlbox(_unit_rect(v, by_id), _html(v, by_id), css=_css(v), archive=_ARCH, scale_low=1)
            if spare >= 0:
                units.append(v)
                break
    sel_ids = {u["id"] for u in units}
    other_lines = [l for u in uj["units"] if u["id"] not in sel_ids for l in (u.get("lines") or [])]
    keep = sorted(set(uj["keep"]) | set(other_lines))
    doc, page = extract.open_page(src, pj["page_index"])
    out = fitz.open()
    out.insert_pdf(doc, from_page=pj["page_index"], to_page=pj["page_index"])
    p = out[0]     # already normalised by open_page; normalising again would shift it
    for u in units:
        for l in u["lines"]:
            for s in by_id[l]["spans"]:
                p.add_redact_annot(fitz.Rect(s["bbox"]) + (0.3, 0.3, -0.3, -0.3), fill=False)
    for l in uj["drop"]:
        p.add_redact_annot(fitz.Rect(by_id[l]["bbox"]) + (0.3, 0.3, -0.3, -0.3), fill=False)
    p.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE, text=fitz.PDF_REDACT_TEXT_REMOVE)
    for u in units:
        rect, scale, color = _unit_rect(u, by_id), 1.0, None
        if mutate:
            rect, scale, color = mutate(u["id"], rect, scale, color)
        p.insert_htmlbox(rect, _html(u, by_id, scale, color), css=_css(u), archive=_ARCH, scale_low=1)
    if region:
        im = max(pj["images"], key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))
        b = fitz.Rect(im["bbox"])
        r = fitz.Rect(b.x0 + 2, b.y1 - 14, b.x1 - 2, b.y1 - 2)
        st_line = next(l for l in pj["lines"] if abs(l["size"] - 6.5) < 0.01)
        ru = {"id": "r1", "region": [round(v, 1) for v in r], "role": "caption", "ja": "(text in picture)",
              "en": [{"t": "Picture text", "style": st_line["id"]}]}
        p.insert_htmlbox(r, _html(ru, by_id), css=_css(ru), archive=_ARCH, scale_low=1)
        units.append(ru)
    out.subset_fonts()
    out.save(str(d / "english.pdf"), garbage=3, deflate=True)
    (d / "units.json").write_text(json.dumps({"units": units, "keep": keep, "drop": uj["drop"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def pick(units_json_dir, roles, cond=None):
    uj = json.loads((units_json_dir / "units.json").read_text(encoding="utf-8"))
    for u in uj["units"]:
        if u.get("role") in roles and (cond is None or cond(u)):
            return u["id"]
    return None


def test_synthetic(tmp):
    cd = build_clean(tmp / "clean")
    clean = checks.check_page(cd)
    errs = [(u["id"], f["check"], f["message"]) for u in clean["units"] for f in u["findings"] if f["severity"] == "error"]
    errs += [("page", f["check"], f["message"]) for f in clean["page"] if f["severity"] == "error"]
    n = len(clean["units"])
    case("synthetic: clean layout of %d units has no errors" % n, not errs, "; ".join("%s %s: %s" % e for e in errs[:8]))
    ids = [u["id"] for u in clean["units"]]
    pj = json.loads((P04 / "page.json").read_text(encoding="utf-8"))
    by_id = {l["id"]: l for l in pj["lines"]}
    uj = {u["id"]: u for u in json.loads((cd / "units.json").read_text(encoding="utf-8"))["units"]}

    def boxed(u):     # sits in a closed shape (container measured on the clean page and not open)
        m = next(x for x in clean["units"] if x["id"] == u["id"])["measured"]
        cb = m.get("container_bbox")
        r = _unit_rect(u, by_id)
        return cb is not None and (cb[2] - cb[0]) < 1.6 * r.width + 6 and (cb[3] - cb[1]) < 2.5 * r.height + 6

    def inj(target, fn):
        return lambda uid, rect, scale, color: fn(rect, scale, color) if uid == target else (rect, scale, color)

    t_box = pick(cd, ("label", "table_header"), boxed)
    t_warn = pick(cd, ("warning",))
    t_open = pick(cd, ("footnote", "body", "caption", "warning"), lambda u: _unit_rect(u, by_id).x1 > 600)
    t_any = pick(cd, ("label", "footnote", "body", "caption"), lambda u: not boxed(u))
    cases = [
        ("too big for its box -> container/collision", t_box, lambda r, s, c: (fitz.Rect(r.x0 - 15, r.y0 - 4, r.x1 + 15, r.y1 + 25), s * 1.4, c),
         ("container", "collision", "margins", "text_overlap")),
        ("pushed past right margin -> margins", t_open, lambda r, s, c: (r + (60, 0, 60, 0), s, c), ("margins",)),
        ("warning shrunk 13% -> size error", t_warn, lambda r, s, c: (r, s * 0.87, c), ("size",)),
        ("warning recoloured -> colour", t_warn, lambda r, s, c: (r, s, 0x000000), ("colour",)),
        ("label shrunk below 5.5pt floor -> size", t_any, lambda r, s, c: (r, 0.6, c), ("size",)),
    ]
    for name, uid, fn, want in cases:
        if uid is None:
            case("synthetic: %s" % name, False, "no suitable unit")
            continue
        res = checks.check_page(build_clean(tmp / ("bad_%s_%s" % (uid, want[0])), inj(uid, fn)))
        got = [f["check"] for f in findings(res, uid) if f["severity"] in ("error", "warn")]
        case("synthetic: %s (%s)" % (name, uid), any(g in want for g in got), "fired: %s" % (got or "nothing"))
    # layout-engine size flags select the threshold; the measured size decides
    single = lambda roles: pick(cd, roles, lambda u: len(u["en"]) == 1)
    t_label = single(("label", "table_header", "table_cell", "caption"))
    t_body = single(("body", "footnote", "bullet"))
    # a warning large enough that 88% of it stays above the 5.5pt floor (below the floor is an error anyway)
    t_w = pick(cd, ("warning",), lambda u: min(T.style_of(g["style"], by_id)["size"] for g in u["en"]) * 0.88 >= 5.6)

    def size_of(uid):
        st = T.style_of(uj[uid]["en"][0]["style"], by_id)
        return st["size"]
    flag_cases = [
        ("flagged warning at 88% -> warn", t_w, lambda o: 0.88, ["fixed_role_shrunk"], "warn"),
        ("flagged warning at 80% -> error", t_w, lambda o: 0.80, ["fixed_role_shrunk"], "error"),
        ("unflagged warning at 88% -> error", t_w, lambda o: 0.88, [], "error"),
        ("flagged label at 4.8pt -> warn", t_label, lambda o: 4.8 / o, ["small_type"], "warn"),
        ("flagged label at 4.2pt -> error", t_label, lambda o: 4.2 / o, ["small_type"], "error"),
        ("flagged body text at 4.8pt -> error", t_body, lambda o: 4.8 / o, ["small_type"], "error"),
    ]
    for name, uid, scale_of, flags, want in flag_cases:
        if uid is None:
            case("size flag: %s" % name, False, "no suitable unit")
            continue
        k = scale_of(size_of(uid))
        dd = build_clean(tmp / ("flag_%s_%s_%d" % (uid, "-".join(flags) or "none", round(k * 100))),
                         inj(uid, lambda r, s, c, k=k: (r + (0, 0, 0, 0), s * k, c)))
        (dd / "placements.json").write_text(json.dumps({"units": [{"id": uid, "flags": flags}]}), encoding="utf-8")
        res = checks.check_page(dd)
        sz = [f for f in findings(res, uid) if f["check"] == "size"]
        got = sz[0]["severity"] if sz else "none"
        case("size flag: %s (%s, %spt)" % (name, uid, sz[0]["evidence"].get("rendered_pt") if sz else "?"), got == want,
             "got %s: %s" % (got, sz[0]["message"] if sz else ""))
    # moved onto a stroke: shift a horizontal unit onto the nearest drawn line below it
    res = checks.check_page(build_clean(tmp / "bad_stroke", inj(t_box, lambda r, s, c: (r + (0, r.height * 0.55, 0, r.height * 0.55), s, c))))
    got = [f["check"] for f in findings(res, t_box) if f["severity"] == "error"]
    case("synthetic: label shifted onto its box border -> collision/container (%s)" % t_box,
         any(g in ("collision", "container") for g in got), "fired: %s" % (got or "nothing"))
    # a unit whose source is a region (Japanese inside a picture)
    rd = build_clean(tmp / "region", region=True)
    res = checks.check_page(rd)
    internal = [f for f in res["page"] if f["check"].startswith("internal")]
    r1 = next((u for u in res["units"] if u["id"] == "r1"), None)
    case("region unit: checked without internal errors", not internal and r1 is not None and r1["measured"].get("ink_bbox") is not None,
         "; ".join(f["message"] for f in internal[:3]))
    # one crashing check must not abort the page
    orig = checks.judge_siblings
    checks.judge_siblings = lambda *a, **k: 1 / 0
    try:
        gd = build_clean(tmp / "guard")
        for f in ("checks.json", "review.png", "compare.png"):
            if (gd / f).exists():
                (gd / f).unlink()
        res = checks.check_page(gd)
    finally:
        checks.judge_siblings = orig
    ok = any(f["check"] == "internal:siblings" and f["severity"] == "error" for f in res["page"]) and \
        all((gd / f).exists() for f in ("checks.json", "review.png", "compare.png")) and len(res["units"]) > 0
    case("a crashing check is reported as a page error and outputs are still written", ok)
    return clean


def main():
    keep = None
    if "--keep" in sys.argv:
        keep = Path(sys.argv[sys.argv.index("--keep") + 1])
    tmp = keep or Path(tempfile.mkdtemp(prefix="checks_selftest_"))
    test_trial(tmp)
    test_original()
    test_synthetic(tmp)
    n_fail = sum(1 for _, ok in results if not ok)
    print("\n%d passed, %d failed" % (len(results) - n_fail, n_fail))
    if keep is None:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
