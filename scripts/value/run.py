"""Run the like-for-like value model on the current rate grid.

    cd scripts && ../.venv/bin/python -m value.run [--json ../reports/like_for_like_value.json] [--tables FILE]

Reads data/ (never writes there). Writes a JSON of every cell's outcomes and,
optionally, the Markdown tables used in reports/Like-for-like value.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np

from . import inputs, projection as pj, validation as vd

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = Path(__file__).resolve().parent / "model_spec.json"


# --------------------------------------------------------------------------
# Arrays from records (assumptions layer)
# --------------------------------------------------------------------------

def cell_arrays(cells):
    """Records -> aligned arrays, shape (n_cell,) or (n_cell, n_years)."""
    n_years = max(c.term_years for c in cells)
    sc = np.full((len(cells), n_years), np.nan)
    for k, c in enumerate(cells):
        if c.surrender_charge_pct is not None:
            sc[k, : c.term_years] = c.surrender_charge_pct
            sc[k, c.term_years:] = 0.0
    params = pj.CellParams(
        crediting_rate_pa=np.array([c.crediting_rate_pa for c in cells]),
        initial_load=np.array([c.initial_load for c in cells]),
        term_years=np.array([c.term_years for c in cells]),
        pays_interest=np.array([1.0 if c.pays_interest else 0.0 for c in cells]),
    )
    extra = {
        "n_years": n_years,
        "surrender_charge": sc,
        "has_mva": np.array([c.has_mva for c in cells]),
        "mva_ref_rate": np.array([c.mva_rate_pa if not math.isnan(c.mva_rate_pa) else c.crediting_rate_pa for c in cells]),
        "cap": np.array([min(1.0 if c.cap_at_premium else np.inf, c.cap_ratio) for c in cells]),
        "mva_rule": tuple(np.array([getattr(c, f) for c in cells]) for f in
                          ("mva_knee_months", "mva_slope_le", "mva_slope_gt", "mva_intercept_months")),
        "sc_on_av": np.array([c.surrender_charge_on_av for c in cells]),
        "mva_spread": np.array([c.mva_spread_pa for c in cells]),
        "spread_in": np.array([c.fx_spread_in_yen for c in cells]),
        "spread_out": np.array([c.fx_spread_out_yen for c in cells]),
    }
    return params, extra


def surrender_paths(proj, params, extra, shocks_pa):
    """Surrender value and return ratio by policy year under each yield shock.

    Returns sv, ratio of shape (n_years + 1, n_shock, n_cell); index 0 is t = 0.
    """
    n_years = extra["n_years"]
    av = proj.account_value[:, 0, :]          # deterministic: scenario-independent roll-up
    cum = proj.cum_payout[:, 0, :]
    shocks = np.asarray(shocks_pa)[:, None]   # (n_shock, 1)
    i_ref = extra["mva_ref_rate"][None, :]
    svs, ratios = [], []
    for t in range(n_years + 1):
        remaining = (params.term_years - t).astype(np.float64)
        if "mva_rule" in extra:
            remaining = pj.mva_effective_years(remaining, *extra["mva_rule"])
        spread = extra.get("mva_spread", np.zeros(len(remaining)))[None, :]
        mult = np.where(extra["has_mva"][None, :], pj.mva_factor(i_ref, i_ref + shocks, remaining, spread), 1.0)
        charge = extra["surrender_charge"][:, t - 1] if t >= 1 else np.full(len(remaining), np.nan)
        base = np.where(extra.get("sc_on_av", np.zeros(len(remaining), bool)), av[t], 1.0)
        sv = pj.surrender_value(av[t][None, :], mult, pj.surrender_charge_amount(charge, base), extra["cap"][None, :])
        sv = np.where((t >= params.term_years)[None, :], av[t][None, :], sv)
        svs.append(sv)
        ratios.append(pj.return_ratio(sv, cum[t][None, :]))
    return np.stack(svs), np.stack(ratios)


# --------------------------------------------------------------------------
# Results (one dict per cell)
# --------------------------------------------------------------------------

def evaluate(cells, market):
    params, extra = cell_arrays(cells)
    n_years = extra["n_years"]
    proj = pj.project(params, n_scen=1, n_years=n_years)
    shocks = np.array(market["yield_shocks_bp"]) / 10000.0
    base = int(np.where(shocks == 0)[0][0])
    sv, ratio = surrender_paths(proj, params, extra, shocks)

    idx = np.arange(len(cells))
    term = params.term_years
    av_term = proj.account_value[term, 0, idx]
    cum_term = proj.cum_payout[term, 0, idx]
    maturity = pj.return_ratio(av_term, cum_term)
    upper = np.where(params.pays_interest > 0, 1.0 + params.crediting_rate_pa * term,
                     (1.0 + params.crediting_rate_pa) ** term)
    cf = pj.buyer_cashflows(proj.payout[:, 0, :], av_term, term, n_years)
    irr_fc = pj.irr(cf)

    spot = np.array([market["fx_ttm"].get(c.currency, np.nan) for c in cells])
    is_jpy = np.array([c.currency == "JPY" for c in cells])
    yen_flat = np.where(is_jpy, maturity,
                        pj.yen_return_ratio(maturity, spot, 0.0, extra["spread_in"], extra["spread_out"]))
    be = pj.breakeven_fx_move(maturity, spot, extra["spread_in"], extra["spread_out"])
    be_ex = pj.breakeven_fx_move(maturity, spot, 0.0, 0.0)
    rt_cost = pj.fx_round_trip_cost(spot, extra["spread_in"], extra["spread_out"])

    # Best complete yen maturity ratio per term (excluding survival-weighted plans, whose
    # death benefit is lower), for the "vs yen product" break-even.
    best_jpy = {}
    for k, c in enumerate(cells):
        if c.currency == "JPY" and not c.survival_weighted and not math.isnan(maturity[k]):
            best_jpy[c.term_years] = max(best_jpy.get(c.term_years, 0.0), maturity[k])

    results = []
    for k, c in enumerate(cells):
        missing = list(c.missing)
        load_ok = not math.isnan(c.initial_load)
        path_ok = load_ok and c.surrender_charge_pct is not None and (not c.has_mva or bool(c.mva_formula_source))
        fx = c.currency != "JPY"
        if fx and math.isnan(spot[k]):
            missing.append("fx_spot (no TTM captured for this currency)")
        spread_ok = not fx or (not math.isnan(c.fx_spread_in_yen) and not math.isnan(spot[k]))
        mid = max(1, min(5, c.term_years - 1))
        mva_impact = {}
        if c.has_mva:
            i_k = extra["mva_ref_rate"][k]
            for t in sorted({1, mid}):
                eff = float(pj.mva_effective_years(c.term_years - t, c.mva_knee_months, c.mva_slope_le,
                                                   c.mva_slope_gt, c.mva_intercept_months))
                base_m = float(pj.mva_factor(i_k, i_k, eff, c.mva_spread_pa))
                mva_impact[f"year{t}_unchanged_rates"] = base_m - 1.0
                for s in market["yield_shocks_bp"]:
                    if s == 0:
                        continue
                    m = float(pj.mva_factor(i_k, i_k + s / 10000.0, eff, c.mva_spread_pa))
                    mva_impact[f"year{t}_{s:+d}bp"] = m / base_m - 1.0
        if not c.has_mva:
            mva_status = "no MVA"
        elif c.mva_formula_source and not math.isnan(c.mva_rate_pa):
            mva_status = "complete" + (f": reference rate at issue {c.mva_rate_basis}" if c.mva_rate_basis else "")
        elif c.mva_formula_source:
            mva_status = "indicative: formula captured, reference rate at issue proxied by the crediting rate"
        else:
            mva_status = "indicative: formula not captured; MSP brochure form ((1+i)/(1+j))^m used with i = published MVA rate" \
                if not math.isnan(c.mva_rate_pa) else \
                "indicative: formula and reference rate not captured; MSP form with i = crediting rate"
        bj = best_jpy.get(c.term_years)
        r = {
            "product_id": c.product_id, "name_ja": c.name_ja, "name_en": c.name_en,
            "plan": c.plan, "plan_en": c.plan_en, "currency": c.currency, "term_years": c.term_years,
            "age_band": c.age_band, "effective_date": c.effective_date,
            "crediting_rate_pct": c.crediting_rate_pa * 100,
            "published_net_yield_pct": None if math.isnan(c.net_yield_pa) else c.net_yield_pa * 100,
            "initial_load_pct": None if not load_ok else c.initial_load * 100,
            "initial_load_source": c.initial_load_source,
            "survival_weighted": c.survival_weighted, "pays_interest": c.pays_interest,
            "rate_metric": c.rate_metric, "comparability_notes": c.notes,
            "surrender_charge_basis": "account_value" if c.surrender_charge_on_av else "premium",
            "maturity_return_ratio_pct": _pct(maturity[k]),
            "maturity_return_ratio_status": "complete" if load_ok else "incomplete",
            "gross_upper_bound_pct": _pct(upper[k]),
            "irr_contract_currency_pct": _pct(irr_fc[k]),
            "return_ratio_5y_pct": _pct(ratio[5, base, k]) if c.term_years >= 5 and n_years >= 5 and (path_ok or c.term_years == 5) else None,
            "return_ratio_10y_pct": _pct(ratio[10, base, k]) if c.term_years >= 10 and n_years >= 10 and (path_ok or c.term_years == 10) else None,
            "surrender_value_path_pct": [_pct(ratio[t, base, k]) for t in range(1, c.term_years + 1)] if path_ok else None,
            "surrender_value_path_status": "complete" if path_ok else "incomplete",
            "surrender_value_path_shocked_pct": {
                f"{s:+d}bp": [_pct(ratio[t, s_i, k]) for t in range(1, c.term_years + 1)]
                for s_i, s in enumerate(market["yield_shocks_bp"]) if s != 0
            } if path_ok and c.has_mva else None,
            "mva_impact_on_value": mva_impact or None,
            "mva_status": mva_status,
            "fx": None if not fx else {
                "spot_ttm": None if math.isnan(spot[k]) else float(spot[k]),
                "spread_in_yen": None if math.isnan(c.fx_spread_in_yen) else c.fx_spread_in_yen,
                "spread_out_yen": None if math.isnan(c.fx_spread_out_yen) else c.fx_spread_out_yen,
                "spread_source": c.fx_spread_source,
                "round_trip_cost_pct": _pct(rt_cost[k]),
                "yen_return_ratio_flat_fx_pct": _pct(yen_flat[k]) if spread_ok else None,
                "breakeven_fx_move_pct": _pct(be[k]) if spread_ok else None,
                "breakeven_fx_move_ex_spread_pct": _pct(be_ex[k]),
                "breakeven_vs_best_jpy_pct": _pct(pj.breakeven_fx_move(maturity[k], spot[k], extra["spread_in"][k],
                                                                       extra["spread_out"][k], bj))
                if (bj and spread_ok) else None,
                "status": "complete" if (spread_ok and load_ok) else "incomplete",
            },
            "missing": missing,
        }
        results.append(r)
    return results


def evaluate_payouts(pcells):
    out = []
    for c in pcells:
        out.append({
            "product_id": c.product_id, "name_ja": c.name_ja, "name_en": c.name_en, "plan": c.plan,
            "plan_en": c.plan_en, "currency": c.currency, "term_years": c.term_years, "age_band": c.age_band,
            "sex": c.sex, "effective_date": c.effective_date, "crediting_rate_pct": c.crediting_rate_pa * 100,
            "payout_rate_pct": c.payout_rate_pa * 100,
            "cumulative_payout_5y_pct": c.payout_rate_pa * 5 * 100,
            "cumulative_payout_10y_pct": float(pj.cumulative_fixed_payout(c.payout_rate_pa, 10)) * 100,
            "return_ratio_status": "incomplete (cumulative payouts only; residual value not modelled)",
            "missing": list(c.missing),
        })
    return out


def _pct(x):
    x = float(x)
    return None if math.isnan(x) else x * 100.0


# --------------------------------------------------------------------------
# Validation against published figures
# --------------------------------------------------------------------------

def validate(products, rates_dir, spec):
    out = {}
    # TDF: zero-load (1+r)^n vs published 実質的な利回り (all current cells).
    rows = []
    for plan in ["jpy_nenkin", "jpy_kaigo", "fx_no_rider", "fx_rider"]:
        cr = inputs.latest_published(inputs.read_rate_csv(inputs.rate_path(rates_dir, "tdf-fiveten-world-4", "crediting_rate", plan)))
        ny = inputs.latest_published(inputs.read_rate_csv(inputs.rate_path(rates_dir, "tdf-fiveten-world-4", "net_yield", plan)))
        for key in sorted(cr):
            if key in ny:
                rows.append((plan, key[0], int(key[1]), float(cr[key]["value_pct"]) / 100, float(ny[key]["value_pct"]) / 100,
                             cr[key]["effective_date"]))
    r, n, t = (np.array([x[3] for x in rows]), np.array([x[4] for x in rows]), np.array([x[2] for x in rows]))
    model, diff, ok = vd.net_yield_reproduction(r, n, t)
    out["tdf_net_yield"] = [
        {"plan": p, "currency": c, "term": tt, "date": d, "crediting_pct": rr * 100, "published_net_pct": nn * 100,
         "model_net_pct": m * 100, "diff_bp": df * 10000, "match": bool(o)}
        for (p, c, tt, rr, nn, d), m, df, o in zip(rows, model, diff, ok)
    ]

    # DFL PCU3 (+ Rakuten): implied initial load across the whole history, split into regimes.
    regimes = []
    for pid in ["dfl-premier-currency-3", "dfl-premier-currency-3-rakuten"]:
        crs = inputs.read_rate_csv(inputs.rate_path(rates_dir, pid, "crediting_rate", "kihon"))
        nys = inputs.read_rate_csv(inputs.rate_path(rates_dir, pid, "net_yield", "kihon"))
        cmap = {(x["currency"], x["term_years"], x["effective_date"]): x for x in crs if x["status"] == "published" and x["value_pct"]}
        series = {}
        for x in nys:
            k = (x["currency"], x["term_years"], x["effective_date"])
            if x["status"] == "published" and x["value_pct"] and k in cmap:
                series.setdefault((x["currency"], int(x["term_years"])), []).append(
                    (x["effective_date"], float(cmap[k]["value_pct"]) / 100, float(x["value_pct"]) / 100))
        for (cur, term), pts in sorted(series.items()):
            pts.sort()
            loads = vd.implied_load_series([p[1] for p in pts], [p[2] for p in pts], term)
            tol = vd.rounding_tolerance_load(term)
            for reg in vd.load_regimes([p[0] for p in pts], list(loads), tol):
                regimes.append({"product_id": pid, "currency": cur, "term": term, "tolerance_pct": tol * 100,
                                **{k: (v * 100 if k in ("mean", "min", "max") else v) for k, v in reg.items()}})
    out["dfl_implied_load_regimes"] = regimes
    out["net_yield_check"] = net_yield_check(rates_dir, spec)
    return out


def _independent_charge(spec, pid, plan, currency, term, rate_pct):
    """(charge, source) for a plan from a brochure or disclosure, never from a 実質利回り.

    The plan's own spec entry first; otherwise the product's other entries, since a 契約時費用 table
    is set per product by currency and term (DFL PCU3 brochure p16). None when there is none.
    """
    entries = [e for e in spec["accumulation_plans"] if e["product_id"] == pid]
    entries.sort(key=lambda e: e["plan"] != plan)
    for e in entries:
        a = e.get("attributes") or {}
        src = str(a.get("initial_charge_source") or "")
        if "initial_charge_pct" not in a or "実質利回り" in src or "net yield" in src.lower():
            continue
        v = inputs.lookup(a["initial_charge_pct"], currency, term, "", rate_pct)
        if isinstance(v, (int, float)):
            return float(v) / 100.0, src.strip('"') + ("" if e["plan"] == plan else f" (product table, from plan {e['plan']})")
    return None


def net_yield_check(rates_dir, spec):
    """Every published 実質利回り in force, against the net yield an independent charge gives.

    status: "match" / "differs" where a brochure charge exists; "no_independent_charge" otherwise
    (the value model then backs the charge out of the same figure, so agreement would prove nothing).
    """
    rows = []
    for path in sorted(rates_dir.glob("*__net_yield__*.csv")):
        pid, _, plan = path.stem.split("__")
        cr = inputs.latest_published(inputs.read_rate_csv(inputs.rate_path(rates_dir, pid, "crediting_rate", plan)))
        for key, ny in sorted(inputs.latest_published(inputs.read_rate_csv(path)).items()):
            c = cr.get(key)
            if not c or c["effective_date"] != ny["effective_date"] or not key[1]:
                continue
            cur, term = key[0], int(key[1])
            r, net = float(c["value_pct"]) / 100, float(ny["value_pct"]) / 100
            base = {"product_id": pid, "plan": plan, "currency": cur, "term_years": term, "age_band": key[2], "sex": key[3],
                    "effective_date": ny["effective_date"], "crediting_pct": r * 100, "published_net_pct": net * 100}
            # A plan that pays out during the term (PGF Neo withdrawal) quotes its 実質利回り on another basis;
            # a charge rolled to maturity says nothing about it.
            if any(e["product_id"] == pid and e["plan"] == plan and e.get("pays_interest") for e in spec["accumulation_plans"]):
                rows.append({**base, "status": "pays_out_not_checked"})
                continue
            ch = _independent_charge(spec, pid, plan, cur, term, r * 100)
            if ch is None:
                rows.append({**base, "status": "no_independent_charge"})
                continue
            model, diff, ok = vd.net_yield_check(r, ch[0], term, net, vd.published_step(ny["value_pct"]))
            rows.append({**base, "charge_pct": ch[0] * 100, "charge_source": ch[1], "model_net_pct": float(model) * 100,
                         "diff_pp": float(diff) * 100, "status": "match" if ok else "differs"})
    return rows


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

def _f(x, d=2, suffix="%"):
    return "–" if x is None else f"{x:.{d}f}{suffix}"


def short_name(r):
    names = {
        "dfl-premier-currency-3": "DFL PCU3", "dfl-premier-currency-3-rakuten": "DFL PCU3 (Rakuten)",
        "msp-mare-3": "MSP MARE3", "sml-tanoshimi-global-3-teiritsu": "SML TG3",
        "tdf-fiveten-world-4": "TDF FTW4", "nwl-yuyu-jikan-advance-2": "NWL YY",
        "dfl-premier-receive-2": "DFL PR2", "dfl-premier-receive-3": "DFL PR3",
    }
    if r["product_id"] in names:
        return names[r["product_id"]]
    return f"{r['product_id'].split('-')[0].upper()} {r.get('name_en') or r['product_id']}"


def _irr_key(r):
    return (r["irr_contract_currency_pct"] is None, -(r["irr_contract_currency_pct"] or 0), -(r["gross_upper_bound_pct"] or 0))


def best_per_plan(rows):
    """One row per (product, plan): the age/sex band with the best buyer yield, tagged with the band count."""
    best = {}
    for r in sorted(rows, key=_irr_key):
        k = (r["product_id"], r["plan"])
        if k not in best:
            best[k] = dict(r, n_bands=0)
        best[k]["n_bands"] += 1
    return list(best.values())


def ranking_tables(results, terms=(5, 10)):
    """Ranking by buyer yield, one row per product plan (best age/sex band; 'best of N' when bands differ)."""
    lines = []
    for cur in ["JPY", "USD", "AUD", "EUR", "NZD"]:
        for term in terms:
            rows = best_per_plan([r for r in results if r["currency"] == cur and r["term_years"] == term])
            if not rows:
                continue
            rows.sort(key=_irr_key)
            lines.append(f"\n#### {cur} {term}Y\n")
            hdr = "| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound |"
            sep = "|---|---|---|---|---|---|---|---|"
            if cur != "JPY":
                hdr += " Yen 返戻率, flat TTM | Break-even FX move | Break-even ex spread |"
                sep += "---|---|---|"
            hdr += " Missing |"
            sep += "---|"
            lines += [hdr, sep]
            rank = 0
            prev = None
            for i, r in enumerate(rows):
                complete = r["maturity_return_ratio_status"] == "complete"
                if complete:
                    if r["irr_contract_currency_pct"] != prev:
                        rank = i + 1
                    prev = r["irr_contract_currency_pct"]
                age = (r["age_band"] or "all") + (f" (best of {r['n_bands']})" if r["n_bands"] > 1 else "")
                row = (f"| {rank if complete else '–'} | {short_name(r)} | {r['plan_en']} | {age} | "
                       f"{_f(r['crediting_rate_pct'])} | {_f(r['maturity_return_ratio_pct'], 1)} | "
                       f"{_f(r['irr_contract_currency_pct'])} | {_f(r['gross_upper_bound_pct'], 1)} |")
                if cur != "JPY":
                    fx = r["fx"]
                    row += (f" {_f(fx['yen_return_ratio_flat_fx_pct'], 1)} | {_f(fx['breakeven_fx_move_pct'], 1)} | "
                            f"{_f(fx['breakeven_fx_move_ex_spread_pct'], 1)} |")
                miss = [m.split(" (")[0] for m in r["missing"]]
                row += f" {', '.join(miss) or 'none'} |"
                lines.append(row)
    return "\n".join(lines)


def surrender_table(results):
    lines = ["| Product | Plan | Ccy | Term | Yr 1 | Yr 2 | Yr 3 | Yr 5 | Yr 1 at +100bp | Yr 1 at −100bp | Maturity |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    seen = set()
    for r in sorted(results, key=lambda r: (r["product_id"], r["plan"], r["currency"], r["term_years"], _irr_key(r))):
        p = r["surrender_value_path_pct"]
        key = (r["product_id"], r["plan"], r["currency"], r["term_years"])
        if not p or key in seen:
            continue
        seen.add(key)
        sh = r["surrender_value_path_shocked_pct"] or {}
        up, dn = (sh.get("+100bp") or [None])[0], (sh.get("-100bp") or [None])[0]
        g = lambda t: _f(p[t - 1], 1) if len(p) >= t else "–"
        lines.append(f"| {short_name(r)} | {r['plan_en']} | {r['currency']} | {r['term_years']} | {g(1)} | {g(2)} | {g(3)} | "
                     f"{g(5)} | {_f(up, 1)} | {_f(dn, 1)} | {_f(p[-1], 1)} |")
    return "\n".join(lines)


def mva_table(results):
    lines = ["| Product | Plan | Ccy | Term | MVA ref. rate | Yr 1: +100bp | Yr 1: −100bp | Mid-term yr | +100bp | −100bp | Status |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    seen = set()
    for r in results:
        m = r["mva_impact_on_value"]
        if not m:
            continue
        key = (r["product_id"], r["plan"], r["currency"], r["term_years"])
        if key in seen:
            continue
        seen.add(key)
        mid = max(1, min(5, r["term_years"] - 1))
        st = r["mva_status"]
        ref = "reconstructed" if st.startswith("complete:") and "reconstructed" in st else \
            "published" if st == "complete" or "published MVA rate" in st else "proxy"
        lines.append(
            f"| {short_name(r)} | {r['plan_en']} | {r['currency']} | {r['term_years']} | "
            f"{ref} | "
            f"{_f(m.get('year1_+100bp', 0) * 100, 1)} | {_f(m.get('year1_-100bp', 0) * 100, 1)} | {mid} | "
            f"{_f(m.get(f'year{mid}_+100bp', 0) * 100, 1)} | {_f(m.get(f'year{mid}_-100bp', 0) * 100, 1)} | "
            f"{r['mva_status'].split(':')[0]} |")
    return "\n".join(lines)


def payout_table(pres):
    lines = ["| Product | Plan | Ccy | Age | Sex | 積立利率 | Payout rate | Paid by yr 5 | Paid by yr 10 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in pres:
        if r["term_years"] != 10:
            continue
        lines.append(f"| {short_name(r)} | {r['plan_en']} | {r['currency']} | {r['age_band']} | {r['sex']} | "
                     f"{_f(r['crediting_rate_pct'])} | {_f(r['payout_rate_pct'])} | {_f(r['cumulative_payout_5y_pct'], 1)} | "
                     f"{_f(r['cumulative_payout_10y_pct'], 1)} |")
    return "\n".join(lines)


def missing_by_product(results, pres):
    agg = {}
    for r in list(results) + list(pres):
        agg.setdefault(r["product_id"], set()).update(m.split(" (")[0] for m in r["missing"])
    return {k: sorted(v) for k, v in sorted(agg.items())}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "reports" / "like_for_like_value.json"))
    ap.add_argument("--tables", default=None, help="write the Markdown tables to this file")
    args = ap.parse_args(argv)

    spec = inputs.load_json(SPEC_PATH)
    products = inputs.load_products(ROOT / "data" / "products")
    rates_dir = ROOT / "data" / "rates"
    cells = inputs.build_cells(products, rates_dir, spec)
    results = evaluate(cells, spec["market"])
    pres = evaluate_payouts(inputs.build_payout_cells(products, rates_dir, spec))
    val = validate(products, rates_dir, spec)
    doc = {
        "about": "Like-for-like value outcomes per unit single premium. See reports/Like-for-like value.md.",
        "market": spec["market"],
        "accumulation": results,
        "payout": pres,
        "not_modelled": spec["not_modelled"],
        "missing_by_product": missing_by_product(results, pres),
        "validation": val,
    }
    Path(args.json).write_text(json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    if args.tables:
        parts = ["## Ranking", ranking_tables(results), "\n## Surrender path", surrender_table(results),
                 "\n## MVA", mva_table(results), "\n## Payout", payout_table(pres),
                 "\n## Missing", json.dumps(doc["missing_by_product"], ensure_ascii=False, indent=1)]
        Path(args.tables).write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"{len(results)} accumulation cells, {len(pres)} payout cells -> {args.json}")


if __name__ == "__main__":
    main()
