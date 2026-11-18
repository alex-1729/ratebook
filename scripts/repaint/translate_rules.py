"""Mechanical pre-pass: lines the program can settle without a model.

Each rule matches the WHOLE text of one line (after Unicode NFKC normalisation, so full-width digits, ％ and ～
match their ASCII forms). A line is only settled by a rule when it stands on its own (see `isolated`): a number
or "円" that is part of a sentence broken across lines is left to the model.

Add rules to RULES. Each rule is a dict:
    name     short name, recorded in translate_run.json
    pattern  regular expression, matched against the whole normalised line text
    action   "keep" (leave as printed) or "en" (replace with the English in `en`)
    en       for "en": the English; {1}, {2} are the pattern's groups. A dict {"1": "...", "*": "..."} picks a
             text by the value of group 1 (for singular/plural), "*" for any other value
    role     for "en": the unit's role
"""
import re
import unicodedata

NUM = r"[+\-−]?\d[\d,]*(?:\.\d+)?"

RULES = [
    # nothing to translate: leave as printed
    {"name": "number", "pattern": NUM, "action": "keep"},
    {"name": "percentage", "pattern": NUM + r"\s?%", "action": "keep"},
    {"name": "percentage range", "pattern": NUM + r"\s?%?\s?[~\-–]\s?" + NUM + r"\s?%", "action": "keep"},
    {"name": "amount with symbol", "pattern": r"[$¥€£]\s?" + NUM, "action": "keep"},
    {"name": "date in digits", "pattern": r"\d{4}[/.\-]\d{1,2}(?:[/.\-]\d{1,2})?", "action": "keep"},
    {"name": "mark", "pattern": r"[▲△▼▽●○◎■□◆◇★☆※*・►▶◀→←↑↓⇒➡]+", "action": "keep"},
    {"name": "circled number", "pattern": r"[①-⑳❶-❿➀-➉]", "action": "keep"},
    {"name": "page reference", "pattern": r"[►▶]?\s?P\.?\s?\d+(?:\s?[・~\-–,、]\s?P?\.?\s?\d+)*", "action": "keep"},
    # standard short labels (prompt glossary)
    {"name": "N年", "pattern": r"(\d+)\s?年", "action": "en", "en": {"1": "1 yr", "*": "{1} yrs"}, "role": "label"},
    {"name": "N年間", "pattern": r"(\d+)\s?年間", "action": "en", "en": {"1": "1 year", "*": "{1} years"}, "role": "label"},
    {"name": "N年ごと", "pattern": r"(\d+)\s?年ごと", "action": "en", "en": {"1": "Every year", "*": "Every {1} yrs"}, "role": "label"},
    {"name": "Nヵ月", "pattern": r"(\d+)\s?[ヵヶかカケ]月", "action": "en", "en": {"1": "1 month", "*": "{1} months"}, "role": "label"},
    {"name": "N歳", "pattern": r"(\d+)\s?歳", "action": "en", "en": "Age {1}", "role": "label"},
    {"name": "N～M歳", "pattern": r"(\d+)\s?歳?\s?[~\-–]\s?(\d+)\s?歳", "action": "en", "en": "Ages {1}–{2}", "role": "label"},
    {"name": "USD", "pattern": r"米ドル", "action": "en", "en": "USD", "role": "label"},
    {"name": "AUD", "pattern": r"豪ドル", "action": "en", "en": "AUD", "role": "label"},
    {"name": "JPY", "pattern": r"円", "action": "en", "en": "JPY", "role": "label"},
]

_COMPILED = [(r, re.compile(r["pattern"])) for r in RULES]


def normalise(text):
    return unicodedata.normalize("NFKC", text).strip()


def _fill(template, groups):
    if isinstance(template, dict):
        template = template.get(groups[0] if groups else "", template["*"])
    for i, g in enumerate(groups, 1):
        template = template.replace("{%d}" % i, str(int(g)) if g.isdigit() else g)
    return template


def match(text):
    """(rule, english or None) for the first rule that matches the whole text, else (None, None)."""
    t = normalise(text)
    for rule, rx in _COMPILED:
        m = rx.fullmatch(t)
        if m:
            if rule["action"] == "keep":
                return rule, None
            return rule, _fill(rule["en"], list(m.groups()))
    return None, None


# ----------------------------------------------------------------------------------------------- isolation guard
def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def same_row(a, b):
    """True if lines a and b sit side by side along the writing direction (separate items split from one PDF
    line, such as table cells or tabs), False if one continues the other on the next row or column."""
    if a.get("vert") != b.get("vert"):
        return False
    ax0, ay0, ax1, ay1 = a["bbox"]
    bx0, by0, bx1, by1 = b["bbox"]
    if a.get("vert"):
        return _overlap(ax0, ax1, bx0, bx1) > 0.5 * min(ax1 - ax0, bx1 - bx0)
    return _overlap(ay0, ay1, by0, by1) > 0.5 * min(ay1 - ay0, by1 - by0)


def block_index(lines):
    idx = {}
    for l in lines:
        idx.setdefault(l.get("block"), []).append(l)
    return idx


def isolated(ids, lines_by_id, blocks):
    """True if the lines `ids` form a piece of text on their own: every other line in their PDF text blocks sits
    on the same row as one of them (a separate item beside it), never above or below (running text)."""
    ids = set(ids)
    for i in ids:
        l = lines_by_id[i]
        for other in blocks.get(l.get("block"), []):
            if other["id"] in ids:
                continue
            if not any(same_row(lines_by_id[j], other) for j in ids):
                return False
    return True


def apply(page, log=None):
    """Mark lines settled by a rule in `page` (in place). Returns {rule name: count}."""
    lines = page["lines"]
    by_id = {l["id"]: l for l in lines}
    blocks = block_index(lines)
    counts = {}
    for l in lines:
        if l.get("done"):
            continue
        rule, en = match(l["text"])
        if rule is None or not isolated([l["id"]], by_id, blocks):
            continue
        l["done"] = True
        l["origin"] = "rule"
        l["rule"] = rule["name"]
        if rule["action"] == "keep":
            l["keep"] = True
        else:
            l["role"] = rule["role"]
            l["en"] = [{"t": en, "style": l["id"]}]
        counts[rule["name"]] = counts.get(rule["name"], 0) + 1
    return counts
