"""The independent net-yield check: a brochure charge rolled forward against the published 実質利回り.

Run:  cd scripts && ../.venv/bin/python -m unittest discover -s value/tests -t .
"""

import unittest

from value import validation as vd


class NetYieldFromCharge(unittest.TestCase):
    def test_hand_calculation(self):
        """r = 5.08%, L = 3.5%, n = 5: 1.0508^5 = 1.281151..., x 0.965 = 1.236311..., ^(1/5) - 1 = 4.33392%."""
        self.assertAlmostEqual(vd.net_yield_from_charge(0.0508, 0.035, 5), 0.0433392, places=7)

    def test_no_charge_returns_crediting_rate(self):
        self.assertAlmostEqual(vd.net_yield_from_charge(0.04, 0.0, 10), 0.04, places=12)


class PublishedStep(unittest.TestCase):
    def test_steps(self):
        self.assertAlmostEqual(vd.published_step("2.310"), 0.00001)
        self.assertAlmostEqual(vd.published_step("4.33"), 0.0001)
        self.assertAlmostEqual(vd.published_step("5"), 0.01)


class Check(unittest.TestCase):
    def test_dfl_usd_5y_matches(self):
        """DFL PCU3 USD 5Y on 16 Nov 2026: 5.08% with the brochure's 3.5% charge gives 4.3339%; published 4.33 (truncated)."""
        _, diff, ok = vd.net_yield_check(0.0508, 0.035, 5, 0.0433, vd.published_step("4.33"))
        self.assertTrue(ok, diff)

    def test_rakuten_usd_10y_matches_brochure_not_implied(self):
        """Rakuten USD 10Y: 5.01% with the disclosure's 3.5% gives 4.6365%; published 4.63. The 3.55% implied earlier was truncation."""
        _, diff, ok = vd.net_yield_check(0.0501, 0.035, 10, 0.0463, vd.published_step("4.63"))
        self.assertTrue(ok, diff)

    def test_wrong_charge_differs(self):
        _, diff, ok = vd.net_yield_check(0.0508, 0.030, 5, 0.0433, vd.published_step("4.33"))
        self.assertFalse(ok)
        self.assertGreater(diff, 0)

    def test_model_below_published_by_a_full_step_differs(self):
        self.assertFalse(vd.net_yield_check(0.0508, 0.040, 5, 0.0433, vd.published_step("4.33"))[2])


if __name__ == "__main__":
    unittest.main()
