"""I/O boundary: read product JSON, rate CSVs and the model spec into typed records.

Nothing here computes a value outcome. Each attribute a cell needs is either
read from data (with its source) or listed in ``Cell.missing``. No defaults.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

NAN = math.nan


@dataclass(frozen=True)
class Cell:
    """One product x plan x currency x term x age band, at the latest rate date."""

    product_id: str
    name_ja: str
    name_en: str
    plan: str
    plan_en: str
    currency: str
    term_years: int
    age_band: str
    effective_date: str
    crediting_rate_pa: float
    net_yield_pa: float = NAN
    mva_rate_pa: float = NAN
    mva_rate_basis: str = ""  # how a spec-held reference rate was obtained when not published as is, e.g. reconstructed
    initial_load: float = NAN
    initial_load_source: str = ""
    surrender_charge_pct: tuple | None = None  # decimals by policy year 1..term
    surrender_charge_source: str = ""
    has_mva: bool = False
    mva_formula_source: str = ""
    cap_at_premium: bool = False
    pays_interest: bool = False
    survival_weighted: bool = False
    fx_spread_in_yen: float = NAN
    fx_spread_out_yen: float = NAN
    fx_spread_source: str = ""
    surrender_charge_on_av: bool = False
    mva_spread_pa: float = 0.0  # c in ((1+i)/(1+j+c))^m; 0 unless captured
    mva_knee_months: float = math.inf
    mva_slope_le: float = 1.0
    mva_slope_gt: float = 1.0
    mva_intercept_months: float = 0.0
    cap_ratio: float = math.inf  # surrender value cap as a fraction of premium
    rate_metric: str = "crediting_rate"
    notes: str = ""
    missing: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class PayoutCell:
    """A periodic-payout whole-life row (定期支払率 / 定期積立率)."""

    product_id: str
    name_ja: str
    name_en: str
    plan: str
    plan_en: str
    currency: str
    term_years: int
    age_band: str
    sex: str
    effective_date: str
    crediting_rate_pa: float
    payout_rate_pa: float
    missing: tuple


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_products(products_dir: Path) -> dict:
    return {p.stem: load_json(p) for p in sorted(products_dir.glob("*.json"))}


def read_rate_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def latest_published(rows: list[dict]) -> dict:
    """Rows at the latest effective_date, published only, keyed by (currency, term, age_band, sex)."""
    if not rows:
        return {}
    last = max(r["effective_date"] for r in rows)
    return {
        (r["currency"], r["term_years"], r["age_band"], r["sex"]): r
        for r in rows
        if r["effective_date"] == last and r["status"] == "published" and r["value_pct"] != ""
    }


def rate_path(rates_dir: Path, product_id: str, metric: str, plan: str) -> Path:
    return rates_dir / f"{product_id}__{metric}__{plan}.csv"


def _pct(r: dict | None) -> float:
    return float(r["value_pct"]) / 100.0 if r else NAN


def _yen_spread(text: str) -> float:
    """'TTM+0.50' -> 0.50."""
    m = re.search(r"([0-9.]+)\s*$", text)
    return float(m.group(1)) if m else NAN


def _captured_initial_load(product: dict, plan: str, term: int):
    loads = product.get("loads") or {}
    by_plan = (loads.get("initial_charge_pct") or {}).get(plan)
    if by_plan is None:
        return None
    value = by_plan.get(str(term)) if isinstance(by_plan, dict) else by_plan
    if value is None:
        return None
    return float(value) / 100.0, f"product JSON loads.initial_charge_pct ({loads.get('source', {}).get('snapshot', '')})"


def _captured_surrender_charge(product: dict, plan: str, currency: str, term: int):
    sc = product.get("surrender_charge")
    if not sc:
        return None
    src = f"product JSON surrender_charge ({sc.get('source', {}).get('snapshot', '')})"
    if plan in (sc.get("applies_to") or []):
        table = sc.get("jpy_pct") if currency == "JPY" else sc.get("foreign_currency_pct")
        if not table:
            return None
        key = "5_year" if term == 5 else "10_and_15_year"
        if key not in table:
            return None
        sched = [float(x) / 100.0 for x in table[key]]
        sched = (sched + [0.0] * term)[:term]
        return tuple(sched), src
    if isinstance(sc.get(plan), str) and "no surrender charge" in sc[plan].lower():
        return tuple([0.0] * term), src
    return None


_BAND = re.compile(r"^\s*(?:([0-9.]+)\s*<=\s*)?rate\s*(?:(>=|<)\s*([0-9.]+))?\s*$")


def _rate_band_match(key: str, rate_pct: float):
    """'rate>=2.50' | '2.00<=rate<2.50' | 'rate<1.50' -> True/False; None if the key is not a band."""
    m = _BAND.match(key)
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    lo, op, x = m.group(1), m.group(2), m.group(3)
    ok = True
    if lo is not None:
        ok = ok and rate_pct >= float(lo) - 1e-9
    if op == ">=":
        ok = ok and rate_pct >= float(x) - 1e-9
    elif op == "<":
        ok = ok and rate_pct < float(x) - 1e-9
    return ok


def lookup(table, currency: str, term: int, age_band: str = "", rate_pct: float | None = None):
    """Read a spec attribute that may be a scalar, a list, or keyed by currency and/or term.

    Accepted shapes: 3.0 | [6.0, 5.4, ...] | {"5": 3.0, "10": 5.0} | {"USD": {...}, "JPY": ...}
    | {"FX": ..., "JPY": ...} | {"all": ...} | {"rate>=2.50": ..., "2.00<=rate<2.50": ..., "rate<2.00": ...}
    (crediting-rate bands, in %; needs ``rate_pct``). Returns None when no entry matches.
    """
    if table is None:
        return None
    if isinstance(table, (int, float, list)):
        return table
    if not isinstance(table, dict):
        return None
    bands = {k: _rate_band_match(k, rate_pct) for k in table} if rate_pct is not None else {}
    if bands and all(v is not None for v in bands.values()):
        hit = [k for k, v in bands.items() if v]
        return lookup(table[hit[0]], currency, term, age_band, rate_pct) if hit else None
    for k in (currency, "FX" if currency != "JPY" else "JPY", age_band or None, "all"):
        if k and k in table and k not in (str(term),):
            inner = table[k]
            if isinstance(inner, dict):
                return lookup(inner, currency, term, age_band, rate_pct)
            return inner
    for k in (str(term), "all"):
        if k in table:
            inner = table[k]
            return lookup(inner, currency, term, age_band, rate_pct) if isinstance(inner, dict) else inner
    # term ranges such as "10-15" or "10_and_15"
    for k, v in table.items():
        nums = [int(x) for x in re.findall(r"\d+", k)]
        if nums and ("-" in k and len(nums) == 2 and nums[0] <= term <= nums[1] or term in nums):
            return lookup(v, currency, term, age_band, rate_pct) if isinstance(v, dict) else v
    return None


def _src(*parts) -> str:
    return " ".join(str(x) for x in parts if x)


def _expand_plans(rates_dir: Path, pid: str, metric: str, plan: str) -> list[str]:
    """Plan keys matching ``plan`` (which may contain a glob) for this product and metric."""
    if any(ch in plan for ch in "*?["):
        prefix = f"{pid}__{metric}__"
        return sorted(p.name[len(prefix):-4] for p in rates_dir.glob(f"{prefix}{plan}.csv"))
    return [plan]


def _series_key(value, plan: str):
    """'same' -> the crediting plan key; None/False -> None; otherwise as given."""
    if value == "same":
        return plan
    return value or None


def build_cells(products: dict, rates_dir: Path, spec: dict) -> list[Cell]:
    """One Cell per published rate row of every accumulation plan in the spec.

    Attribute precedence: structured product JSON (e.g. MSP MAREⅢ ``loads``) >
    plan ``attributes`` in the spec > product ``supplement`` in the spec > missing.
    """
    cells: list[Cell] = []
    supplement = spec.get("supplement", {})
    for ps in spec["accumulation_plans"]:
        pid = ps["product_id"]
        product = products.get(pid)
        if product is None:
            continue
        sup = supplement.get(pid, {})
        attrs = ps.get("attributes", {})
        metric = ps.get("rate_metric", "crediting_rate")
        for plan in _expand_plans(rates_dir, pid, metric, ps["plan"]):
            cr = latest_published(read_rate_csv(rate_path(rates_dir, pid, metric, plan)))
            ny_key = None if ps.get("net_yield") == "equals_crediting" else _series_key(ps.get("net_yield"), plan)
            ny = latest_published(read_rate_csv(rate_path(rates_dir, pid, "net_yield", ny_key))) if ny_key else {}
            mv_key = _series_key(ps.get("mva_rate"), plan)
            mv = latest_published(read_rate_csv(rate_path(rates_dir, pid, ps.get("mva_rate_metric", "mva_rate"), mv_key))) \
                if mv_key else {}
            for key, row in sorted(cr.items()):
                currency, term_s, age_band, sex = key
                term_override = ps.get("term_years")
                by_age = (ps.get("term_by_age_band") or {}).get(age_band)
                if term_override is not None:
                    term = int(term_override)
                elif not term_s and by_age:
                    term = int(by_age)
                elif term_s:
                    term = int(term_s)
                else:
                    continue
                if term < 1 or (ps.get("terms") and term not in ps["terms"]) \
                        or (ps.get("age_bands") and age_band not in ps["age_bands"]) \
                        or (ps.get("sexes") and sex not in ps["sexes"]):
                    continue
                missing = []
                rate = _pct(row)

                # Initial load / net yield
                net = _pct(ny.get(key) or ny.get((currency, term_s, age_band, "")) or ny.get((currency, term_s, "", "")))
                load, load_src = NAN, ""
                captured = _captured_initial_load(product, plan, term)
                spec_load = lookup(attrs.get("initial_charge_pct"), currency, term, age_band, rate * 100)
                eq = attrs.get("net_yield_equals_crediting") or sup.get("net_yield_equals_crediting")
                eq_applies = bool(eq) and (not eq.get("plans") or plan in eq["plans"]) \
                    if isinstance(eq, dict) else bool(eq)
                if captured is not None:
                    load, load_src = captured
                elif spec_load is not None:
                    load = float(spec_load) / 100.0
                    load_src = _src("spec attributes.initial_charge_pct:", attrs.get("initial_charge_source"))
                elif not math.isnan(net):
                    load = 1.0 - ((1.0 + net) / (1.0 + rate)) ** term
                    load_src = f"implied from published 実質利回り {net * 100:.3f}%"
                elif eq_applies:
                    load, net = 0.0, rate
                    load_src = eq.get("source", "") if isinstance(eq, dict) else str(attrs.get("net_yield_equals_crediting_source", ""))
                else:
                    missing.append("initial_load (no load, 実質利回り or net-equals-crediting statement captured)")

                # Surrender charge (decimals by policy year; basis premium or account value)
                sc = _captured_surrender_charge(product, plan, currency, term)
                sc_basis = "premium"
                if sc is None:
                    sched = lookup(attrs.get("surrender_charge_pct"), currency, term, age_band, rate * 100)
                    if isinstance(sched, (int, float)):
                        sched = [sched] * term
                    if isinstance(sched, list):
                        vals = [float(x) / 100.0 for x in sched]
                        vals = (vals + [vals[-1] if attrs.get("surrender_charge_extend_last") else 0.0] * term)[:term]
                        sc = (tuple(vals), _src("spec attributes.surrender_charge_pct:", attrs.get("surrender_charge_source")))
                        sc_basis = attrs.get("surrender_charge_basis", "premium")
                if sc is None:
                    missing.append("surrender_charge schedule")
                    sc_pct, sc_src = None, ""
                else:
                    sc_pct, sc_src = sc

                # MVA
                has_mva = bool(product.get("has_mva")) if attrs.get("has_mva") is None else bool(attrs["has_mva"])
                mva_rate = _pct(mv.get(key) or mv.get((currency, term_s, age_band, "")) or mv.get((currency, term_s, "", "")))
                mva_src = ""
                mva_basis = ""
                if has_mva:
                    formula = (product.get("mva") or {}).get("formula") or attrs.get("mva_formula") or sup.get("mva_formula")
                    if formula:
                        mva_src = _src("MVA formula:", formula, attrs.get("mva_formula_source") or sup.get("mva_formula_source"))
                    else:
                        missing.append("mva_formula")
                    if math.isnan(mva_rate):
                        # Published reference rate held in the spec (insurer pages without a rate CSV in data/).
                        tbl = attrs.get("mva_reference_rate_pct")
                        if isinstance(tbl, dict) and plan in tbl:  # glob entries: values keyed by plan key first
                            tbl = tbl[plan]
                        ref = lookup(tbl, currency, term, age_band)
                        if isinstance(ref, (int, float)):
                            mva_rate = float(ref) / 100.0
                            mva_src = _src(mva_src, "| MVA reference rate at issue:", attrs.get("mva_reference_rate_source"))
                            mva_basis = attrs.get("mva_reference_rate_basis", "")
                    if math.isnan(mva_rate):
                        missing.append("mva_reference_rate (市場価格調整用利率) at issue")

                # FX spread
                si = so = NAN
                fx_src = ""
                if currency != "JPY":
                    fxj = (product.get("loads") or {}).get("fx_spread_jpy")
                    fxs = attrs.get("fx_spread_jpy") or sup.get("fx_spread_jpy")
                    if fxj:
                        si, so = _yen_spread(fxj["yen_premium"]), _yen_spread(fxj["yen_payout"])
                        fx_src = "product JSON loads.fx_spread_jpy"
                    elif fxs:
                        si = fxs.get("spread_in_yen", fxs.get("in"))
                        so = fxs.get("spread_out_yen", fxs.get("out"))
                        fx_src = fxs.get("source", "")
                    else:
                        missing.append("fx_spread (yen conversion rate vs TTM)")

                cap = bool(attrs.get("cap_at_premium")) or \
                    "capped at" in str(((product.get("plan_terms") or {}).get(plan) or {}).get("surrender_value", ""))
                missing += list(ps.get("extra_missing", []))

                cells.append(Cell(
                    product_id=pid, name_ja=product.get("name_ja", ""), name_en=product.get("name_en", ""),
                    plan=plan, plan_en=ps.get("plan_en", plan) if plan == ps["plan"] else f"{ps.get('plan_en', '')} [{plan}]".strip(),
                    currency=currency, term_years=term,
                    age_band=age_band + (f" {sex}" if sex else ""), effective_date=row["effective_date"],
                    crediting_rate_pa=rate, net_yield_pa=net, mva_rate_pa=mva_rate, mva_rate_basis=mva_basis, initial_load=load,
                    initial_load_source=load_src, surrender_charge_pct=sc_pct, surrender_charge_source=sc_src,
                    surrender_charge_on_av=(sc_basis == "account_value"), has_mva=has_mva,
                    mva_formula_source=mva_src, cap_at_premium=cap, pays_interest=bool(ps.get("pays_interest")),
                    survival_weighted=bool(ps.get("survival_weighted")), fx_spread_in_yen=si,
                    fx_spread_out_yen=so, fx_spread_source=fx_src, rate_metric=metric,
                    mva_spread_pa=float(lookup(attrs.get("mva_spread_pct", sup.get("mva_spread_pct", 0.0)),
                                               currency, term, age_band) or 0.0) / 100.0,
                    mva_knee_months=float((attrs.get("mva_months_rule") or sup.get("mva_months_rule") or {}).get("knee", math.inf)),
                    mva_slope_le=float((attrs.get("mva_months_rule") or sup.get("mva_months_rule") or {}).get("slope_le", 1.0)),
                    mva_slope_gt=float((attrs.get("mva_months_rule") or sup.get("mva_months_rule") or {}).get("slope_gt", 1.0)),
                    mva_intercept_months=float((attrs.get("mva_months_rule") or sup.get("mva_months_rule") or {}).get("intercept", 0.0)),
                    cap_ratio=float(attrs["cap_ratio"]) if attrs.get("cap_ratio") is not None else math.inf,
                    notes=ps.get("comparability_notes", ""), missing=tuple(missing),
                ))
    return cells


def build_payout_cells(products: dict, rates_dir: Path, spec: dict) -> list[PayoutCell]:
    out: list[PayoutCell] = []
    for ps in spec.get("payout_plans", []):
        pid = ps["product_id"]
        product = products.get(pid)
        if product is None:
            continue
        for plan in _expand_plans(rates_dir, pid, ps["payout_metric"], ps["plan"]):
            cr = latest_published(read_rate_csv(rate_path(rates_dir, pid, ps.get("rate_metric", "crediting_rate"), plan)))
            pr = latest_published(read_rate_csv(rate_path(rates_dir, pid, ps["payout_metric"], plan)))
            for key, row in sorted(pr.items()):
                currency, term_s, age_band, sex = key
                term = int(ps.get("term_years") or term_s or 0)
                if term < 1:
                    continue
                crow = cr.get((currency, term_s, age_band, "")) or cr.get(key)
                missing = tuple(ps.get("missing", (
                    "account value after payouts (how 定期支払 draws on the 積立金)",
                    "surrender_charge schedule", "mva_formula", "death benefit after payouts")))
                out.append(PayoutCell(
                    product_id=pid, name_ja=product.get("name_ja", ""), name_en=product.get("name_en", ""),
                    plan=plan, plan_en=ps.get("plan_en", plan) if plan == ps["plan"] else f"{ps.get('plan_en', '')} [{plan}]",
                    currency=currency, term_years=term, age_band=age_band, sex=sex,
                    effective_date=row["effective_date"], crediting_rate_pa=_pct(crow), payout_rate_pa=_pct(row),
                    missing=missing + (("fx_spread (yen conversion rate vs TTM)",)
                                       if currency != "JPY" and not ps.get("fx_spread_jpy") else ()),
                ))
    return out
