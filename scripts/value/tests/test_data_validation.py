"""Checks against the repository's data: published figures and completeness flags.

These read data/ (read only). They pin the validation results stated in
reports/Like-for-like value.md; if new rate rows change a result, the report
needs updating too.
"""

import copy
import math
import unittest

import numpy as np

from value import inputs, run, validation as vd

ROOT = run.ROOT


class PublishedFigures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.products = inputs.load_products(ROOT / "data" / "products")
        cls.val = run.validate(cls.products, ROOT / "data" / "rates", inputs.load_json(run.SPEC_PATH))

    def test_dfl_net_yield_matches_brochure_charge(self):
        """Every current DFL PCU3 (+ Rakuten) 実質利回り is reproduced by the brochure's 契約時費用, not by one backed out of it."""
        rows = [r for r in self.val["net_yield_check"] if r["product_id"].startswith("dfl-premier-currency-3")]
        self.assertGreaterEqual(len(rows), 14)
        self.assertEqual([r for r in rows if r["status"] != "match"], [])

    def test_every_checked_net_yield_matches(self):
        """No published 実質利回り that the check covers disagrees with its brochure charge (the app would show ≠)."""
        self.assertEqual([r for r in self.val["net_yield_check"] if r["status"] == "differs"], [])

    def test_plans_that_pay_out_are_not_checked(self):
        rows = [r for r in self.val["net_yield_check"] if r["product_id"] == "pgf-hosho-sentaku-neo-usd" and r["plan"] == "withdrawal"]
        self.assertTrue(rows)
        self.assertTrue(all(r["status"] == "pays_out_not_checked" for r in rows))

    def test_no_check_rests_on_a_charge_from_the_net_yield(self):
        for r in self.val["net_yield_check"]:
            if "charge_source" in r:
                self.assertNotIn("実質利回り", r["charge_source"])

    def test_tdf_rollup_reproduces_published_net_yield(self):
        """(1+積立利率)^n reproduces every current TDF 実質的な利回り to the insurer's 2-dp truncation."""
        rows = self.val["tdf_net_yield"]
        self.assertGreaterEqual(len(rows), 16)
        bad = [r for r in rows if not r["match"]]
        self.assertEqual(bad, [])
        # The two AUD 5Y rows are the only ones that differ (by the truncation, under 1bp).
        diffs = [r for r in rows if abs(r["diff_bp"]) > 1e-6]
        self.assertEqual({(r["currency"], r["term"]) for r in diffs}, {("AUD", 5)})

    def test_dfl_implied_load_is_piecewise_constant(self):
        """DFL PCU3 implied initial load is constant within rounding between a few change dates."""
        regs = [r for r in self.val["dfl_implied_load_regimes"] if r["product_id"] == "dfl-premier-currency-3"]
        by_key = {}
        for r in regs:
            by_key.setdefault((r["currency"], r["term"]), []).append(r)
        for key, rs in by_key.items():
            self.assertLessEqual(len(rs), 4, key)  # at most a handful of charge changes since 2023-04
            for r in rs:
                self.assertLessEqual(r["max"] - r["min"], r["tolerance_pct"] + 1e-9, key)
        cur = {k: rs[-1]["mean"] for k, rs in by_key.items()}
        self.assertAlmostEqual(cur[("USD", 5)], 3.5, delta=0.05)
        self.assertAlmostEqual(cur[("USD", 10)], 4.1, delta=0.1)
        self.assertAlmostEqual(cur[("JPY", 5)], 1.5, delta=0.05)
        self.assertAlmostEqual(cur[("JPY", 10)], 2.2, delta=0.05)

    def test_products_and_rates_sheet_figures(self):
        """The current DFL PCU3 basic rates in Products and rates.md feed the model unchanged."""
        spec = inputs.load_json(run.SPEC_PATH)
        cells = inputs.build_cells(self.products, ROOT / "data" / "rates", spec)
        c = {(x.product_id, x.plan, x.currency, x.term_years): x for x in cells}
        k = c[("dfl-premier-currency-3", "kihon", "USD", 5)]
        self.assertAlmostEqual(k.crediting_rate_pa, 0.0508)
        self.assertAlmostEqual(k.net_yield_pa, 0.0433)
        k = c[("dfl-premier-currency-3", "kihon", "JPY", 10)]
        self.assertAlmostEqual(k.crediting_rate_pa, 0.0265)
        self.assertAlmostEqual(k.net_yield_pa, 0.02421)


class Completeness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = inputs.load_json(run.SPEC_PATH)
        products = inputs.load_products(ROOT / "data" / "products")
        cls.cells = inputs.build_cells(products, ROOT / "data" / "rates", spec)
        cls.results = run.evaluate(cls.cells, spec["market"])
        no_spot = copy.deepcopy(spec["market"])
        del no_spot["fx_ttm"]["EUR"]
        cls.no_spot = run.evaluate(cls.cells, no_spot)
        # Every live product now has its charges and FX spread captured, so the missing-attribute
        # paths are exercised on a copy of the spec with PGF's attributes stripped.
        stripped = copy.deepcopy(spec)
        for e in stripped["accumulation_plans"]:
            if e["product_id"] == "pgf-hosho-sentaku-neo-usd":
                e["attributes"] = {"has_mva": True}
                e["net_yield"] = None
        cls.stripped = run.evaluate(inputs.build_cells(products, ROOT / "data" / "rates", stripped), spec["market"])
        no_fx = copy.deepcopy(spec)
        for e in no_fx["accumulation_plans"]:
            if e["product_id"] == "pgf-hosho-sentaku-neo-usd":
                e["attributes"].pop("fx_spread_jpy")
        cls.no_fx = run.evaluate(inputs.build_cells(products, ROOT / "data" / "rates", no_fx), spec["market"])

    def _get(self, pid, plan, cur, term, age="", results=None):
        return next(r for r in (results or self.results)
                    if (r["product_id"], r["plan"], r["currency"], r["term_years"], r["age_band"]) == (pid, plan, cur, term, age))

    def test_tdf_yen_plans_use_reconstructed_jgb_reference_rate(self):
        """T&D yen plans take i from the JGB reconstruction in the spec (5y/10y by deferral for FTW4 and YYN2;
        20y for PW6 ages 40-79, 10y for 80-90) and say it is reconstructed; FX plans keep the crediting-rate proxy."""
        tdf = ("tdf-fiveten-world-4", "tdf-yasashii-yen-nenkin-2", "tdf-premium-world-6")
        yen = [(c, r) for c, r in zip(self.cells, self.results) if c.product_id in tdf and c.currency == "JPY"]
        self.assertEqual({c.product_id for c, _ in yen}, set(tdf))
        for c, r in yen:
            if c.product_id == "tdf-premium-world-6":
                want = 3.61 if c.age_band.split()[0] in ("40-69", "70-79") else 2.72
            else:
                want = {5: 2.10, 10: 2.72}[c.term_years]
            self.assertAlmostEqual(c.mva_rate_pa * 100, want, places=9, msg=(c.plan, c.term_years, c.age_band))
            self.assertIn("reconstructed from JGB par yields", r["mva_status"])
            self.assertTrue(r["mva_status"].startswith("complete:"))
            self.assertIn("jgbcm_2026-11-17.csv", c.mva_formula_source)
        fx = [(c, r) for c, r in zip(self.cells, self.results) if c.product_id in tdf and c.currency != "JPY"]
        self.assertTrue(fx)
        for c, r in fx:
            self.assertTrue(math.isnan(c.mva_rate_pa))
            self.assertTrue(r["mva_status"].startswith("indicative"))

    def test_missing_load_gives_no_value(self):
        r = self._get("pgf-hosho-sentaku-neo-usd", "base", "USD", 20, "15-79", self.stripped)
        self.assertIsNone(r["maturity_return_ratio_pct"])
        self.assertEqual(r["maturity_return_ratio_status"], "incomplete")
        self.assertTrue(any(m.startswith("initial_load") for m in r["missing"]))
        self.assertIsNotNone(r["gross_upper_bound_pct"])

    def test_missing_surrender_charge_gives_no_path(self):
        r = self._get("pgf-hosho-sentaku-neo-usd", "base", "USD", 20, "15-79", self.stripped)
        self.assertIsNone(r["surrender_value_path_pct"])
        self.assertIsNone(r["return_ratio_5y_pct"])
        self.assertIsNotNone(r["gross_upper_bound_pct"])

    def test_missing_fx_spread_gives_no_yen_value(self):
        r = self._get("pgf-hosho-sentaku-neo-usd", "base", "USD", 20, "15-79", self.no_fx)
        self.assertIsNone(r["fx"]["yen_return_ratio_flat_fx_pct"])
        self.assertIsNotNone(r["fx"]["breakeven_fx_move_ex_spread_pct"])
        self.assertIn("fx_spread", " ".join(r["missing"]))

    def test_missing_fx_spot_gives_no_yen_value(self):
        r = self._get("msp-mare-3", "tsumitate", "EUR", 10, "", self.no_spot)
        self.assertIsNone(r["fx"]["yen_return_ratio_flat_fx_pct"])
        self.assertIn("fx_spot", " ".join(r["missing"]))

    def test_msp_eur_and_nzd_have_yen_values(self):
        """EUR (TTM 175.40 printed) and NZD (TTM 87.73, midpoint of printed TTS 88.23 / TTB 87.23) now convert at +/-0.50."""
        for ccy, spot in (("EUR", 175.40), ("NZD", 87.73)):
            r = self._get("msp-mare-3", "tsumitate", ccy, 10)
            self.assertNotIn("fx_spot", " ".join(r["missing"]))
            self.assertAlmostEqual(r["fx"]["yen_return_ratio_flat_fx_pct"],
                                   r["maturity_return_ratio_pct"] * (spot - 0.5) / (spot + 0.5), places=8)

    def test_msp_complete_path_and_values(self):
        """MSP MAREⅢ 積立コース USD 10Y: 4.79% x 10y, no load; year-1 surrender: 1.0479 - 6% = 98.79%; year 5: 1.0479^5 - 3.6%."""
        r = self._get("msp-mare-3", "tsumitate", "USD", 10)
        self.assertAlmostEqual(r["maturity_return_ratio_pct"], 100 * 1.0479 ** 10, places=8)
        self.assertAlmostEqual(r["surrender_value_path_pct"][0], 100 * (1.0479 - 0.06), places=8)
        self.assertAlmostEqual(r["return_ratio_5y_pct"], 100 * (1.0479 ** 5 - 0.036), places=8)
        self.assertAlmostEqual(r["fx"]["yen_return_ratio_flat_fx_pct"],
                               100 * 1.0479 ** 10 * (159.48 - 0.5) / (159.48 + 0.5), places=8)

    def test_msp_annuity_fund_course_load_and_cap(self):
        """年金原資充実コース USD 10Y: 5% initial charge; surrender value capped at the premium."""
        r = self._get("msp-mare-3", "nenkin_genshi_jujitsu", "USD", 10)
        self.assertAlmostEqual(r["maturity_return_ratio_pct"], 100 * 0.95 * 1.0536 ** 10, places=8)
        self.assertTrue(all(v <= 100 + 1e-9 for v in r["surrender_value_path_pct"][:-1]))

    def test_no_value_is_defaulted(self):
        """Every cell with a NaN attribute lists it as missing."""
        for c in self.cells:
            if math.isnan(c.initial_load):
                self.assertTrue(any(m.startswith("initial_load") for m in c.missing), c)
            if c.surrender_charge_pct is None:
                self.assertTrue(any(m.startswith("surrender_charge") for m in c.missing), c)
            if c.currency != "JPY" and math.isnan(c.fx_spread_in_yen):
                self.assertTrue(any(m.startswith("fx_spread") for m in c.missing), c)


class InsurerExamples(unittest.TestCase):
    """Worked figures from insurer brochures (collected by the research agents) reproduced by the model."""

    @classmethod
    def setUpClass(cls):
        spec = inputs.load_json(run.SPEC_PATH)
        products = inputs.load_products(ROOT / "data" / "products")
        cls.cells = inputs.build_cells(products, ROOT / "data" / "rates", spec)

    def _cell(self, pid, plan, cur, term, age=""):
        return next(c for c in self.cells if (c.product_id, c.plan, c.currency, c.term_years) == (pid, plan, cur, term)
                    and c.age_band.startswith(age))

    def test_dfl_brochure_charge_matches_implied_load(self):
        """DFL brochure 契約時費用 (FX 5y 3.5 / 10y 4.1; JPY 5y 1.5 / 10y 2.2) vs the load implied by 実質利回り."""
        for cur, term, pct in [("USD", 5, 3.5), ("USD", 10, 4.1), ("AUD", 10, 4.1), ("JPY", 5, 1.5), ("JPY", 10, 2.2)]:
            c = self._cell("dfl-premier-currency-3", "kihon", cur, term)
            tol = vd.rounding_tolerance_load(term) + 0.0005
            self.assertAlmostEqual(c.initial_load, pct / 100, delta=tol, msg=(cur, term))

    def test_rakuten_brochure_charge_matches_implied_load(self):
        for cur, term, pct in [("USD", 5, 3.1), ("USD", 10, 3.5), ("JPY", 5, 1.3), ("JPY", 10, 1.9)]:
            c = self._cell("dfl-premier-currency-3-rakuten", "kihon", cur, term)
            tol = vd.rounding_tolerance_load(term) + 0.0005
            self.assertAlmostEqual(c.initial_load, pct / 100, delta=tol, msg=(cur, term))

    def test_orix_example(self):
        """ORIX Moonshot pamphlet: 100,000 USD at 4.5% for 10 years -> 155,296 (no initial charge)."""
        c = self._cell("orx-moonshot", "standard", "USD", 10)
        self.assertEqual(c.initial_load, 0.0)
        self.assertAlmostEqual(100_000 * (1 - c.initial_load) * 1.045 ** 10, 155_296, delta=1)

    def test_msp_ashita_example(self):
        """MSP Ashita no Yorokobi 2 積立コース: 100,000 at 3.9% for 10 years -> 146,607."""
        c = self._cell("msp-ashitano-yorokobi-2", "tsumitate", "USD", 10)
        self.assertEqual(c.initial_load, 0.0)
        self.assertAlmostEqual(100_000 * 1.039 ** 10, 146_607, delta=1)

    def test_nli_mva_table(self):
        """NLI Longdream Gold 3 brochure: reference rate 2.0% at both dates, margin 0.1% -> MVA deduction 0.98% (10y), 0.49% (5y), 0.10% (1y)."""
        from value import projection as pj
        c = self._cell("nli-longdream-gold3", "fuyasu_no_rider", "USD", 10)
        for years, pct in [(10, 0.98), (5, 0.49), (1, 0.10)]:
            f = pj.mva_factor(np.array(0.02), np.array(0.02), np.array(float(years)), c.mva_spread_pa)
            self.assertAlmostEqual(100 * (1 - float(f)), pct, delta=0.006)

    def test_nwl_designated_currency_example(self):
        """NWL 指定通貨建個人年金保険 しおり 2026年7月版 p44: USD 10y, 積立金 104,567.84 at 36 months, i=2.00%, j=1.00%,
        c=0.10%, k=1, charge 3.15% of 100,000 -> 108,110.18; with j=3.00% -> 93,857.59."""
        from value import projection as pj
        c = self._cell("nwl-form-designated-currency-annuity", "prem_all", "USD", 10, "0-84")
        self.assertAlmostEqual(c.surrender_charge_pct[3], 0.0315)
        yrs = pj.mva_effective_years(np.array(7.0), c.mva_knee_months, c.mva_slope_le, c.mva_slope_gt, c.mva_intercept_months)
        for j, mva_rate_pct, figure in [(0.01, -6.40, 108_110.18), (0.03, 7.23, 93_857.59)]:
            f = float(pj.mva_factor(np.array(0.02), np.array(j), yrs, c.mva_spread_pa))
            self.assertAlmostEqual(round(100 * (1 - f), 2), mva_rate_pct)  # the insurer rounds the MVA rate to 0.01%
            self.assertAlmostEqual(104_567.84 * (1 - mva_rate_pct / 100) - 100_000 * 0.0315, figure, delta=0.01)

    def test_pgf_mva_example(self):
        """PGF 保障選択型終身保険Neo brochure p26: i=j=3%, A=0.10% -> MVA deduction 1.54% (20-year period, months x0.80)
        and 0.87% (15-year period, months x0.60), at the start of the period."""
        from value import projection as pj
        for term, age, pct in [(20, "15-79", 1.54), (15, "80-90", 0.87)]:
            c = self._cell("pgf-hosho-sentaku-neo-usd", "base", "USD", term, age)
            yrs = pj.mva_effective_years(np.array(float(term)), c.mva_knee_months, c.mva_slope_le, c.mva_slope_gt,
                                         c.mva_intercept_months)
            f = float(pj.mva_factor(np.array(0.03), np.array(0.03), yrs, c.mva_spread_pa))
            self.assertAlmostEqual(100 * (1 - f), pct, delta=0.006)

    def test_published_reference_rates(self):
        """MVA i from the insurers' rate pages for the current window: ORIX 平均指標金利 USD 5.84 / JPY 3.48,
        MSP 指標金利 USD 10y 5.71, PGF 基準利率 20年 6.04 / 15年 5.58, DFL Partner 市場価格調整用利率 per plan."""
        for pid, plan, cur, term, age, pct in [
                ("orx-moonshot", "standard", "USD", 10, "", 5.84), ("orx-moonshot", "standard", "JPY", 10, "", 3.48),
                ("msp-mare-3", "tsumitate", "USD", 10, "", 5.71), ("msp-ashitano-yorokobi-2", "mankiki_jujitsu_g100", "JPY", 20, "", 3.68),
                ("pgf-hosho-sentaku-neo-usd", "base", "USD", 20, "15-79", 6.04), ("pgf-hosho-sentaku-neo-usd", "base", "USD", 15, "80-90", 5.58),
                ("dfl-premier-partner", "kihon_kakutei_pay10_def5-6", "USD", 5, "", 6.20),
                ("dfl-premier-partner", "kihon_kakutei_pay25-40_def7-10", "JPY", 10, "", 3.60)]:
            c = self._cell(pid, plan, cur, term, age)
            self.assertAlmostEqual(100 * c.mva_rate_pa, pct, places=6, msg=(pid, plan, cur))
            self.assertFalse(any("mva_reference_rate" in m for m in c.missing), (pid, plan, cur))

    def test_tdf_mva_terms(self):
        """T&D PW6 期間係数 (約款 p56-57): yen 40-69 male 0.75 / female 0.85, 85-90 male 0.40; USD 1.0, AUD 0.8.
        会社の定める率: brochure maximum, yen 0.05% / FX 0.10%."""
        for plan, cur, term, age, k in [("jpy_std", "JPY", 30, "40-69 M", 0.75), ("jpy_std", "JPY", 30, "40-69 F", 0.85),
                                        ("jpy_std", "JPY", 15, "85-90 M", 0.40), ("fx_rider", "USD", 10, "", 1.0),
                                        ("fx_rider", "AUD", 20, "", 0.8)]:
            c = self._cell("tdf-premium-world-6", plan, cur, term, age)
            self.assertAlmostEqual(c.mva_slope_le, k, msg=(plan, cur, age))
            self.assertAlmostEqual(c.mva_spread_pa, 0.0005 if cur == "JPY" else 0.0010)
        self.assertAlmostEqual(self._cell("tdf-fiveten-world-4", "jpy_kaigo", "JPY", 10).mva_spread_pa, 0.0005)

    def test_rate_band_selects_receive_charge(self):
        """DFL Premier Receive 2: USD (rate >= 2.50%) age 0-65 -> 6.0% in year 1; JPY 2.97% (rate >= 1.00) -> 2.5%."""
        usd = self._cell("dfl-premier-receive-2", "kihon", "USD", 10, "0-65")
        jpy = self._cell("dfl-premier-receive-2", "kihon", "JPY", 10, "0-65")
        self.assertAlmostEqual(usd.surrender_charge_pct[0], 0.06)
        self.assertAlmostEqual(jpy.surrender_charge_pct[0], 0.025)


class RegimeSplitter(unittest.TestCase):
    def test_hand_example(self):
        """0.035, 0.0352, 0.0349 within 0.0005 -> one regime; 0.041 starts another."""
        regs = vd.load_regimes(["a", "b", "c", "d"], [0.035, 0.0352, 0.0349, 0.041], 0.0005)
        self.assertEqual([(r["start"], r["end"], r["n"]) for r in regs], [("a", "c", 3), ("d", "d", 1)])

    def test_net_yield_reproduction_truncation(self):
        model, diff, ok = vd.net_yield_reproduction(np.array([0.0372, 0.0372]), np.array([0.0371, 0.0369]),
                                                    np.array([5, 5]))
        self.assertEqual(list(ok), [True, False])


if __name__ == "__main__":
    unittest.main()
