"""Hand-calculation, invariant and reference-implementation tests for value.projection.

Run:  cd scripts && ../.venv/bin/python -m unittest discover -s value/tests -t .
"""

import math
import unittest

import numpy as np

from value import projection as pj


def params(rate, load=0.0, term=3, pays=0.0):
    a = lambda x: np.atleast_1d(np.asarray(x, dtype=np.float64))
    return pj.CellParams(a(rate), a(load), np.atleast_1d(np.asarray(term)), a(pays))


class HandCalculations(unittest.TestCase):
    def test_roll_up_three_years(self):
        """AV0 = 1 x (1 - 0.02) = 0.98; r = 5%.
        t1: 0.98 x 1.05 = 1.029; t2: 1.08045; t3: 1.1344725. No payouts."""
        p = pj.project(params(0.05, 0.02, 3), n_scen=1, n_years=3)
        np.testing.assert_allclose(p.account_value[:, 0, 0], [0.98, 1.029, 1.08045, 1.1344725], rtol=0, atol=1e-12)
        np.testing.assert_allclose(p.cum_payout[:, 0, 0], 0.0)

    def test_frozen_after_term(self):
        """Term 2 at 10%: 1, 1.1, 1.21, then 1.21 at t3 (paid at maturity, no more interest)."""
        p = pj.project(params(0.10, 0.0, 2), n_scen=1, n_years=3)
        np.testing.assert_allclose(p.account_value[:, 0, 0], [1.0, 1.1, 1.21, 1.21], atol=1e-12)

    def test_interest_paid_out(self):
        """定期支払: r = 4%, AV stays 1; payouts 0.04 each year; cum 0.12 after 3 years.
        返戻率 at maturity = 1 + 0.12 = 112%."""
        p = pj.project(params(0.04, 0.0, 3, pays=1.0), n_scen=1, n_years=3)
        np.testing.assert_allclose(p.account_value[:, 0, 0], 1.0, atol=1e-12)
        np.testing.assert_allclose(p.payout[:, 0, 0], [0, 0.04, 0.04, 0.04], atol=1e-12)
        self.assertAlmostEqual(pj.return_ratio(p.account_value[3, 0, 0], p.cum_payout[3, 0, 0]), 1.12, places=12)

    def test_mva_factor(self):
        """i = 3%, j = 4%, 2 years left: (1.03/1.04)^2 = 0.980862..."""
        self.assertAlmostEqual(float(pj.mva_factor(0.03, 0.04, 2.0)), (1.03 / 1.04) ** 2, places=14)
        self.assertEqual(float(pj.mva_factor(0.03, 0.03, 7.0)), 1.0)
        self.assertEqual(float(pj.mva_factor(0.03, 0.05, 0.0)), 1.0)

    def test_surrender_value(self):
        """AV 1.10, MVA 0.95 -> 1.045; charge 6% of premium -> 0.985.
        With a premium cap: min(1.045, 1.0) - 0 = 1.0."""
        self.assertAlmostEqual(float(pj.surrender_value(1.10, 0.95, 0.06, np.inf)), 0.985, places=12)
        self.assertAlmostEqual(float(pj.surrender_value(1.10, 0.95, 0.0, 1.0)), 1.0, places=12)
        self.assertEqual(float(pj.surrender_value(0.05, 0.5, 0.06, np.inf)), 0.0)

    def test_implied_load(self):
        """cr 5.08%, net 4.33%, 5 years: 1 - (1.0433/1.0508)^5 = 0.035181..."""
        self.assertAlmostEqual(float(pj.implied_initial_load(0.0508, 0.0433, 5)),
                               1 - (1.0433 / 1.0508) ** 5, places=14)
        self.assertEqual(float(pj.implied_initial_load(0.03, 0.03, 10)), 0.0)

    def test_yen_return_and_breakeven(self):
        """R = 1.20, TTM 150, spreads 0.5/0.5, flat FX: 1.20 x 149.5/150.5 = 1.192026...
        Break-even: (150.5/1.2 + 0.5)/150 - 1 = 125.91667/150 - 1 = -0.160556."""
        y = float(pj.yen_return_ratio(1.20, 150.0, 0.0, 0.5, 0.5))
        self.assertAlmostEqual(y, 1.20 * 149.5 / 150.5, places=12)
        x = float(pj.breakeven_fx_move(1.20, 150.0, 0.5, 0.5))
        self.assertAlmostEqual(x, (150.5 / 1.2 + 0.5) / 150 - 1, places=12)
        self.assertAlmostEqual(float(pj.yen_return_ratio(1.20, 150.0, x, 0.5, 0.5)), 1.0, places=12)
        self.assertAlmostEqual(float(pj.fx_round_trip_cost(150.0, 0.5, 0.5)), 1 - 149.5 / 150.5, places=12)

    def test_breakeven_vs_target(self):
        """Target 1.10 yen ratio: x solves R(S(1+x)-so)/(S+si) = 1.10."""
        x = float(pj.breakeven_fx_move(1.30, 100.0, 0.5, 0.5, 1.10))
        self.assertAlmostEqual(float(pj.yen_return_ratio(1.30, 100.0, x, 0.5, 0.5)), 1.10, places=12)

    def test_irr(self):
        """-1, 0, 1.1025 -> 5%; -1, 0.04, 0.04, 1.04 -> 4%."""
        cf = np.array([[-1.0, -1.0], [0.0, 0.04], [1.1025, 0.04], [0.0, 1.04]])
        np.testing.assert_allclose(pj.irr(cf), [0.05, 0.04], atol=1e-10)

    def test_cumulative_fixed_payout(self):
        """5.08% a year for 10 years = 50.8% of premium."""
        self.assertAlmostEqual(float(pj.cumulative_fixed_payout(0.0508, 10)), 0.508, places=12)


class Invariants(unittest.TestCase):
    def test_zero_load_no_payout_is_compound_rollup(self):
        rates = np.array([0.01, 0.0262, 0.0508, 0.0561])
        terms = np.array([5, 10, 10, 15])
        p = pj.project(pj.CellParams(rates, np.zeros(4), terms, np.zeros(4)), n_scen=2, n_years=15)
        for k in range(4):
            for t in range(16):
                self.assertAlmostEqual(p.account_value[t, 1, k], (1 + rates[k]) ** min(t, terms[k]), places=12)

    def test_av_non_decreasing_with_non_negative_rates(self):
        rng = np.random.default_rng(1)
        n = 50
        p = pj.project(pj.CellParams(rng.uniform(0, 0.08, n), rng.uniform(0, 0.06, n),
                                     rng.integers(1, 21, n), rng.integers(0, 2, n).astype(float)), 1, 20)
        self.assertTrue(np.all(np.diff(p.account_value, axis=0) >= -1e-15))
        self.assertTrue(np.all(np.diff(p.cum_payout, axis=0) >= -1e-15))

    def test_value_conserved(self):
        """AV_t + cum_payout_t = AV_0 + all interest credited, whether or not interest is paid out."""
        a = pj.project(params([0.03, 0.03], [0.01, 0.01], [10, 10], [0.0, 1.0]), 1, 10)
        total = a.account_value + a.cum_payout
        # Paying out interest means no compounding, so the paid-out plan has less in total but
        # its AV is constant at AV0.
        np.testing.assert_allclose(a.account_value[:, 0, 1], 0.99, atol=1e-12)
        np.testing.assert_allclose(total[10, 0, 1], 0.99 * (1 + 0.3), atol=1e-12)
        np.testing.assert_allclose(total[10, 0, 0], 0.99 * 1.03 ** 10, atol=1e-12)

    def test_irr_equals_net_yield_for_single_maturity(self):
        r, L, n = 0.0495, 0.0411, 10
        ratio = (1 - L) * (1 + r) ** n
        cf = np.zeros((n + 1, 1)); cf[0] = -1; cf[n] = ratio
        self.assertAlmostEqual(float(pj.irr(cf)[0]), float(pj.annualised_yield(ratio, n)), places=10)

    def test_inputs_not_mutated(self):
        p = params([0.05, 0.02], [0.01, 0.0], [3, 5])
        before = [x.copy() for x in p]
        pj.project(p, 2, 6)
        for b, a in zip(before, p):
            np.testing.assert_array_equal(b, a)

    def test_scenarios_identical_for_rollup(self):
        p = pj.project(params([0.05, 0.02], [0.01, 0.0], [3, 5]), 3, 6)
        np.testing.assert_array_equal(p.account_value[:, 0, :], p.account_value[:, 2, :])


def reference_single_cell(rate, load, term, pays, n_years, i_ref, shock, sc, cap):
    """Readable loop: one cell, one scenario. Returns (av path, cum payouts, return ratio path)."""
    av = 1.0 * (1 - load)
    cum = 0.0
    avs, cums, ratios = [av], [cum], [math.nan]
    for t in range(1, n_years + 1):
        if t <= term:
            interest = av * rate
            pay = interest if pays else 0.0
            av = av + interest - pay
            cum += pay
        avs.append(av)
        cums.append(cum)
        if t >= term:
            sv = av
        else:
            mult = ((1 + i_ref) / (1 + i_ref + shock)) ** (term - t)
            sv = max(min(av * mult, cap) - sc[t - 1], 0.0)
        ratios.append(sv + cum)
    return np.array(avs), np.array(cums), np.array(ratios)


class ReferenceImplementation(unittest.TestCase):
    def test_vectorised_matches_loop(self):
        from value.run import surrender_paths

        rng = np.random.default_rng(7)
        n, n_years = 40, 15
        rate = rng.uniform(0.0, 0.07, n)
        load = rng.uniform(0.0, 0.06, n)
        term = rng.integers(2, 16, n)
        pays = rng.integers(0, 2, n).astype(float)
        cap = np.where(rng.random(n) < 0.3, 1.0, np.inf)
        sc = rng.uniform(0, 0.06, (n, n_years))
        for k in range(n):
            sc[k, term[k]:] = 0.0
        i_ref = rate + rng.uniform(-0.01, 0.01, n)
        shocks = np.array([-0.01, 0.0, 0.01])
        p = pj.CellParams(rate, load, term, pays)
        proj = pj.project(p, 1, n_years)
        extra = {"n_years": n_years, "surrender_charge": sc, "has_mva": np.ones(n, bool),
                 "mva_ref_rate": i_ref, "cap": cap}
        _, ratio = surrender_paths(proj, p, extra, shocks)
        for k in range(n):
            for s_i, s in enumerate(shocks):
                avs, cums, ratios = reference_single_cell(rate[k], load[k], term[k], pays[k], n_years,
                                                          i_ref[k], s, sc[k], cap[k])
                np.testing.assert_allclose(proj.account_value[:, 0, k], avs, rtol=0, atol=1e-10)
                np.testing.assert_allclose(proj.cum_payout[:, 0, k], cums, rtol=0, atol=1e-10)
                np.testing.assert_allclose(ratio[1:, s_i, k], ratios[1:], rtol=0, atol=1e-10)


if __name__ == "__main__":
    unittest.main()


class MvaSpread(unittest.TestCase):
    def test_spread_bites_at_unchanged_rates(self):
        """i = j = 3%, c = 0.10%, 5 years left: (1.03/1.031)^5 = 0.995160..."""
        self.assertAlmostEqual(float(pj.mva_factor(0.03, 0.03, 5.0, 0.001)), (1.03 / 1.031) ** 5, places=14)


class MvaDuration(unittest.TestCase):
    def test_dfl_rule(self):
        """DFL: 15 years left = 180 months -> 0.5 x 180 + 60 = 150 months = 12.5 years; 8 years stays 8."""
        np.testing.assert_allclose(pj.mva_effective_years(np.array([15.0, 8.0]), 120, 1.0, 0.5, 60), [12.5, 8.0])

    def test_metlife_rule(self):
        """MetLife: 24 months -> 0.81 x 24 = 19.44 months; 180 months -> 0.52 x 247 = 128.44 months."""
        np.testing.assert_allclose(pj.mva_effective_years(np.array([2.0, 15.0]), 120, 0.81, 0.52, 0.52 * 67),
                                   [19.44 / 12, 128.44 / 12])

    def test_default_is_identity(self):
        np.testing.assert_allclose(pj.mva_effective_years(np.array([0.0, 3.0, 20.0]), np.inf, 1.0, 1.0, 0.0), [0, 3, 20])
