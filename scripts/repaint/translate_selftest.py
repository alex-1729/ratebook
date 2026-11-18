"""Self-test for the pre-pass rules, the translation memory and its guards. No model, no network.

    .venv/bin/python scripts/repaint/translate_selftest.py

Uses a temporary memory folder (REPAINT_MEMORY_DIR), never translations/_memory.
"""
import copy
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="repaint-mem-")
os.environ["REPAINT_MEMORY_DIR"] = TMP
import translate_memory as M  # noqa: E402
import translate_rules as R  # noqa: E402
import units as U  # noqa: E402

failures = 0


def check(label, cond, detail=""):
    global failures
    failures += not cond
    print("%-4s %s%s" % ("ok" if cond else "FAIL", label, "" if cond else "  " + str(detail)))


def line(i, text, bbox, block, size=10, spans=None, vert=False):
    return {"id": i, "block": block, "bbox": bbox, "vert": vert, "text": text, "size": size, "color": 0, "weight": 400,
            "spans": [{"text": t, "bbox": bbox, "size": size} for t in (spans or [text])]}


def page(lines):
    return {"extract_version": 2, "source": "x.pdf", "page_index": 0, "width": 600, "height": 800, "lines": lines,
            "duplicates": [], "outlined": [], "images": []}


# ---- rules: whole-line patterns
for text, want in [("10年", "10 yrs"), ("１年", "1 yr"), ("5年ごと", "Every 5 yrs"), ("0～85歳", "Ages 0–85"),
                   ("米ドル", "USD"), ("円", "JPY"), ("2.2％", "keep"), ("P30～32", "keep"), ("1,000", "keep"),
                   ("10年後", None), ("約10年", None), ("12月", None), ("米ドル建", None), ("年金", None)]:
    rule, en = R.match(text)
    got = None if rule is None else ("keep" if rule["action"] == "keep" else en)
    check("rule %-8s -> %s" % (text, want), got == want, got)

# ---- isolation: a rule line inside running text is left to the model
p = page([line(0, "保険料は100万", [10, 10, 200, 20], 0), line(1, "円", [10, 22, 20, 32], 0),
          line(2, "円", [300, 10, 310, 20], 1), line(3, "10年", [400, 10, 430, 20], 2), line(4, "5年", [450, 10, 470, 20], 2)])
R.apply(p)
check("rule: 円 continuing a sentence is not settled", not p["lines"][1].get("done"))
check("rule: 円 alone in its block is settled", p["lines"][2].get("en") == [{"t": "JPY", "style": 2}])
check("rule: side-by-side cells split from one line are settled", p["lines"][3].get("done") and p["lines"][4].get("done"))

# ---- memory: store a validated page, then look it up on another page
src = page([line(0, "積立金額", [10, 10, 60, 20], 0), line(1, "ご契約時に年金原資が", [10, 40, 200, 50], 1),
            line(2, "確定します。", [10, 52, 100, 62], 1), line(3, "死亡給付金", [10, 90, 80, 100], 2),
            line(4, "について", [82, 90, 120, 100], 2), line(5, "見出し", [10, 120, 60, 135], 3, size=14),
            line(6, "本文の続き", [10, 137, 90, 147], 3)])
units = {"units": [
    {"id": "u1", "lines": [0], "role": "label", "en": [{"t": "Account value", "style": 0}], "en_short": [{"t": "Acct value", "style": 0}]},
    {"id": "u2", "lines": [1, 2], "role": "body", "en": [{"t": "At inception the annuity principal is fixed.", "style": 1}]},
    {"id": "u3", "lines": [3], "role": "heading", "en": [{"t": "About the death benefit", "style": 3}]},
    {"id": "u4", "lines": [5], "role": "heading", "en": [{"t": "Heading", "style": 5}]},
    {"id": "u5", "lines": [6], "role": "body", "en": [{"t": "Rest of text", "style": 6}]}],
    "keep": [], "drop": [4], "notes": []}
check("memory: source units are valid", not U.validate(units, src), U.validate(units, src))
n = M.store(units, src, {"pdf": "a.pdf", "page": 1, "backend": "test", "model": "test"})
check("memory: stores isolated units only (not the one beside a dropped particle, not part of a block)", n == 2, n)

tgt = page([line(0, "ご契約時に年金原資が", [10, 40, 200, 50], 0), line(1, "確定します。", [10, 52, 100, 62], 0),
            line(2, "積立金額", [300, 10, 350, 20], 1), line(3, "積立金額", [10, 200, 60, 210], 2, size=30),
            line(4, "積立金額は増えます", [10, 300, 200, 310], 3), line(5, "積立金額", [10, 312, 60, 322], 3)])
groups = M.apply(tgt)
got = {tuple(g["lines"]): g for g in groups}
check("memory: two-line paragraph matched, styles re-pointed", (0, 1) in got and got[(0, 1)]["en"][0]["style"] == 0, groups)
check("memory: label matched with en_short", (2,) in got and got[(2,)].get("en_short") == [{"t": "Acct value", "style": 2}])
check("memory: much larger text is not filled", (3,) not in got)
check("memory: same text continuing a sentence is not filled", (5,) not in got)

# conflicting roles for one text are not used
M.store({"units": [{"id": "u1", "lines": [0], "role": "heading", "en": [{"t": "Account value", "style": 0}]}],
         "keep": [], "drop": [], "notes": []}, page([line(0, "積立金額", [10, 10, 60, 20], 0)]),
        {"pdf": "b.pdf", "page": 2, "backend": "test", "model": "test"})
tgt2 = page([line(0, "積立金額", [300, 10, 350, 20], 1)])
check("memory: text stored with two roles is not used", M.apply(tgt2) == [])

# ---- page hash
a = page([line(0, "積立金額", [10, 10, 60, 20], 0)])
b = copy.deepcopy(a)
b["lines"][0]["done"] = True
c = copy.deepcopy(a)
c["lines"][0]["bbox"] = [10, 30, 60, 40]
check("page hash ignores pre-pass marks", M.page_hash(a, "P", []) == M.page_hash(b, "P", []))
check("page hash changes with position, prompt and pictures",
      len({M.page_hash(a, "P", []), M.page_hash(c, "P", []), M.page_hash(a, "Q", []), M.page_hash(a, "P", ["ff"])}) == 4)

print("\n%s" % ("all passed" if not failures else "%d FAILED" % failures))
sys.exit(1 if failures else 0)
