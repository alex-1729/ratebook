"""Self-test for units.py, no model needed.

    .venv/bin/python scripts/repaint/units_selftest.py

The units.json files in translations/_work/{dfl-pcu3-p04,dfl-pcu3-p09,tdf-ftw4-p04} must pass;
broken copies of one of them must each fail with the expected message.
"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import units as U  # noqa: E402

WORK = HERE.parent.parent / "translations" / "_work"
GOOD = ["dfl-pcu3-p04", "dfl-pcu3-p09", "tdf-ftw4-p04"]


def load(name):
    d = WORK / name
    return (json.loads((d / "units.json").read_text(encoding="utf-8")),
            json.loads((d / "page.json").read_text(encoding="utf-8")))


def first(units, pred):
    return next(u for u in units["units"] if pred(u))


def lined(u):
    return "lines" in u


def mutations(page):
    """(name, function that breaks a units dict in place, text the problem must contain)."""
    ids = [l["id"] for l in page["lines"]]
    multi_span = next(l for l in page["lines"] if len(l["spans"]) >= 1)

    def m(f):
        return f

    return [
        ("not an object", lambda u: u.clear() or u.update({"units": "x"}), "no \"units\" list"),
        ("two sources", m(lambda u: first(u, lined).update({"region": [10, 10, 50, 30]})), "exactly one source"),
        ("no source", m(lambda u: first(u, lined).pop("lines")), "exactly one source"),
        ("line used twice", m(lambda u: u["keep"].append(first(u, lined)["lines"][0])), "is used more than once"),
        ("line missing", m(lambda u: first(u, lambda x: len(x.get("lines", [])) > 1)["lines"].pop()), "not used anywhere"),
        ("unknown line", m(lambda u: first(u, lined)["lines"].append(99999)), "which page.json does not have"),
        ("unknown outlined", m(lambda u: first(u, lined).update({"outlined": "o99"}) or first(u, lined).pop("lines")),
         "outlined id o99 is not in page.json"),
        ("region outside page", m(lambda u: u["units"].append({"id": "ux", "region": [0, 0, 2000, 3000], "role": "label",
                                                               "ja": "図", "en": [{"t": "Figure", "style": ids[0]}]})),
         "goes outside the page"),
        ("region upside down", m(lambda u: u["units"].append({"id": "ux", "region": [100, 100, 50, 50], "role": "label",
                                                              "ja": "図", "en": [{"t": "Figure", "style": ids[0]}]})),
         "empty or upside down"),
        ("bad role", m(lambda u: u["units"][0].update({"role": "title"})), "role \"title\" is not one of"),
        ("empty en", m(lambda u: u["units"][0].update({"en": []})), "must be a non-empty list"),
        ("empty text", m(lambda u: u["units"][0]["en"].append({"t": "", "style": ids[0]})), "has empty text"),
        ("break-only segment is fine with real text", None, None),
        ("bad style", m(lambda u: u["units"][0]["en"][0].update({"style": 99999})), "style 99999 is not a line id"),
        ("bad span", m(lambda u: u["units"][0]["en"][0].update({"style": "%d:%d" % (multi_span["id"], len(multi_span["spans"]))})),
         "names span"),
        ("garbage style", m(lambda u: u["units"][0]["en"][0].update({"style": "bold"})), "is not a line id or \"line:span\""),
        ("Japanese left in en", m(lambda u: u["units"][0]["en"][0].update({"t": "Account 積立金額"})), "still contains Japanese"),
        ("Japanese left in en_short", m(lambda u: first(u, lambda x: "en_short" in x)["en_short"][0].update({"t": "ご注意"})),
         "en_short segment 1 still contains Japanese"),
        ("en_short shape differs", m(lambda u: first(u, lambda x: "en_short" in x)["en_short"].append({"t": "extra", "style": ids[0]})),
         "en_short must have the same segments"),
        ("layout hint in segment", m(lambda u: u["units"][0]["en"][0].update({"size": 9})), "keys the program does not use: size"),
        ("layout hint in unit", m(lambda u: u["units"][0].update({"align": "center"})), "Do not give layout instructions"),
        ("duplicate unit id", m(lambda u: u["units"][1].update({"id": u["units"][0]["id"]})), "used for more than one unit"),
        ("no ja on outlined unit", m(lambda u: first(u, lambda x: "outlined" in x).pop("ja")), "has no \"ja\""),
        ("bad origin", m(lambda u: u["units"][0].update({"origin": "guess"})), "origin \"guess\" is not one of"),
        ("style missing on lines unit", m(lambda u: u["units"][0]["en"][0].pop("style")), "has no \"style\""),
        ("keep not ints", m(lambda u: u.update({"keep": ["3"]})), "\"keep\" must be a list of line ids"),
    ]


def main():
    failures = 0
    for name in GOOD:
        units, page = load(name)
        probs = U.validate(units, page)
        ok = not probs
        failures += not ok
        print("%-4s valid file %s%s" % ("ok" if ok else "FAIL", name, "" if ok else ": " + "; ".join(probs[:3])))
        try:
            U.load_units(WORK / name)
        except ValueError as e:
            failures += 1
            print("FAIL load_units(%s): %s" % (name, e))

    units0, page = load(GOOD[0])
    for name, breaker, expect in mutations(page):
        u = copy.deepcopy(units0)
        if breaker is None:   # a positive variant: a break-only segment
            u["units"][0]["en"].insert(0, {"t": "", "br": True})
            probs = U.validate(u, page)
            ok = not probs
            print("%-4s variant: %s%s" % ("ok" if ok else "FAIL", name, "" if ok else " -> " + "; ".join(probs)))
            failures += not ok
            continue
        breaker(u)
        probs = U.validate(u, page)
        ok = any(expect in p for p in probs)
        failures += not ok
        print("%-4s broken: %-28s -> %s" % ("ok" if ok else "FAIL", name, probs[0] if ok and len(probs) == 1 else
                                            (next(p for p in probs if expect in p) if ok else "expected %r, got %s" % (expect, probs[:3]))))

    # variants that must PASS under the revised prompt
    def ok_variant(label, f, pg=None, model_part=False):
        nonlocal failures
        u = copy.deepcopy(units0)
        pg = pg or page
        f(u)
        probs = U.validate(u, pg, model_part)
        ok = not probs
        failures += not ok
        print("%-4s variant: %s%s" % ("ok" if ok else "FAIL", label, "" if ok else " -> " + "; ".join(probs[:3])))

    ok_variant("lines units without ja", lambda u: [x.pop("ja", None) for x in u["units"] if "lines" in x])
    ok_variant("outlined unit without style", lambda u: [s.pop("style") for x in u["units"] if "outlined" in x for s in x["en"]])
    ok_variant("region unit without style", lambda u: u["units"].append(
        {"id": "ux", "region": [10, 10, 60, 30], "role": "label", "ja": "図", "en": [{"t": "Figure"}]}))
    ok_variant("origin and page_hash", lambda u: (u["units"][0].update({"origin": "memory"}), u["units"][1].update({"origin": "rule"}),
                                                   u.update({"page_hash": "abc"})))

    # model_part: lines marked done in page.json are left out of the model's file
    pg = copy.deepcopy(page)
    u1 = first(units0, lined)
    done = set(u1["lines"])
    for l in pg["lines"]:
        if l["id"] in done:
            l["done"] = True
    ok_variant("model part leaves done lines out", lambda u: u["units"].remove(first(u, lambda x: x["id"] == u1["id"])),
               pg, model_part=True)
    u = copy.deepcopy(units0)
    probs = U.validate(u, pg, True)
    ok = any("marked \"done\"" in p for p in probs)
    failures += not ok
    print("%-4s broken: %-28s -> %s" % ("ok" if ok else "FAIL", "model part uses a done line", probs[:1]))
    probs = U.validate(u, pg, False)
    ok = not probs
    failures += not ok
    print("%-4s variant: finished file may use done lines%s" % ("ok" if ok else "FAIL", "" if ok else " -> %s" % probs[:2]))

    # file-level errors through load_units
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "page.json").write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        for label, content, expect in (("missing units.json", None, "was not found"),
                                       ("not JSON", "{\"units\": [", "is not valid JSON")):
            if content is not None:
                (td / "units.json").write_text(content, encoding="utf-8")
            try:
                U.load_units(td)
                ok, msg = False, "no error raised"
            except ValueError as e:
                ok, msg = expect in str(e), str(e)
            failures += not ok
            print("%-4s file: %-30s -> %s" % ("ok" if ok else "FAIL", label, msg.splitlines()[0]))

    print("\n%s" % ("all passed" if not failures else "%d FAILED" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
