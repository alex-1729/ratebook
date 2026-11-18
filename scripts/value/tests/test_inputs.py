"""Tests for spec-attribute lookup and the surrender-charge basis."""

import unittest

import numpy as np

from value import inputs, projection as pj
from value.run import surrender_paths


class Lookup(unittest.TestCase):
    def test_shapes(self):
        L = inputs.lookup
        self.assertEqual(L(3.0, "USD", 10), 3.0)
        self.assertEqual(L({"5": 3.0, "10": 5.0}, "USD", 10), 5.0)
        self.assertIsNone(L({"5": 3.0}, "USD", 10))
        self.assertEqual(L({"USD": {"10": 4.1}, "JPY": {"10": 2.2}}, "JPY", 10), 2.2)
        self.assertEqual(L({"FX": 1.0, "JPY": 0.5}, "AUD", 5), 1.0)
        self.assertEqual(L({"10-15": [6.0, 5.4]}, "USD", 15), [6.0, 5.4])
        self.assertEqual(L({"5_year": [4.0], "10_and_15_year": [6.0]}, "USD", 15), [6.0])
        self.assertEqual(L({"all": 2.0}, "USD", 7), 2.0)
        self.assertIsNone(L(None, "USD", 5))

    def test_rate_bands(self):
        L = inputs.lookup
        t = {"rate>=2.50": [6.0], "2.00<=rate<2.50": [5.4], "rate<2.00": [4.6]}
        self.assertEqual(L(t, "USD", 10, rate_pct=5.01), [6.0])
        self.assertEqual(L(t, "USD", 10, rate_pct=2.50), [6.0])
        self.assertEqual(L(t, "USD", 10, rate_pct=2.0), [5.4])
        self.assertEqual(L(t, "USD", 10, rate_pct=1.99), [4.6])
        nested = {"JPY": {"0-75": {"rate>=1.00": [2.5], "rate<1.00": [1.7]}}}
        self.assertEqual(L(nested, "JPY", 10, "0-75", 2.97), [2.5])
        self.assertIsNone(L({"rate>=3.00": [5.0]}, "USD", 10, rate_pct=2.9))


class SurrenderBasis(unittest.TestCase):
    def test_charge_on_account_value(self):
        """AV0 = 1, r = 10%, term 3, charge 5% of AV in year 1, no MVA move:
        on premium: 1.1 - 0.05 = 1.05; on AV: 1.1 x 0.95 = 1.045."""
        p = pj.CellParams(np.array([0.1, 0.1]), np.zeros(2), np.array([3, 3]), np.zeros(2))
        proj = pj.project(p, 1, 3)
        sc = np.array([[0.05, 0, 0], [0.05, 0, 0]])
        extra = {"n_years": 3, "surrender_charge": sc, "has_mva": np.zeros(2, bool),
                 "mva_ref_rate": np.array([0.1, 0.1]), "cap": np.full(2, np.inf),
                 "sc_on_av": np.array([False, True])}
        _, ratio = surrender_paths(proj, p, extra, np.array([0.0]))
        np.testing.assert_allclose(ratio[1, 0], [1.05, 1.045], atol=1e-12)


if __name__ == "__main__":
    unittest.main()
