"""Like-for-like value projection for single-premium savings products.

Turns each product's published rates and captured attributes into what a
buyer gets back: 返戻率 (return ratio) at maturity, the surrender value path
by policy year, the MVA effect of a parallel yield shock, and for
foreign-currency products the yen-basis return after the insurer's FX spread.

Layers
------
- ``inputs``      I/O boundary: reads ``data/products/*.json``, ``data/rates/*.csv``
                  and ``scripts/value/model_spec.json``; returns typed records.
                  Every attribute carries its source, and an attribute that is
                  not captured is recorded as missing, never defaulted.
- ``projection``  Pure array functions: account-value step and driver,
                  surrender value, MVA factor, return ratios, IRR, FX.
- ``validation``  Pure checks of model output against published figures.
- ``run``         Orchestration: cells -> arrays -> projection -> JSON + tables.

Conventions
-----------
- Period: one policy year. Japanese single-premium products quote 積立利率 as an
  annual effective rate and credit it annually, and the outputs asked for are
  by policy year, so no monthly conversion is made. ``_pa`` rates are decimals
  (0.0508, not 5.08).
- Timing: ``_bop`` is just after the premium on day 0 of a policy year,
  ``_eop`` is the policy anniversary. A surrender "at year t" happens just
  before the t-th anniversary: the year-t surrender charge applies and the MVA
  runs over ``term - t`` remaining years. At maturity (t = term) neither applies.
- Decrements: none. Values are per surviving policy. Survival-weighted
  (tontine-style) plans pay less on death; that is a caveat, not modelled.
- Sign: cashflows are from the buyer's side. Premium negative, receipts positive.
- Units: per 1 unit of single premium in the contract currency, unless a name
  says ``_yen``. A return ratio of 1.0 means 100% (返戻率 100%).
- Shapes: ``n_cell`` = product x plan x currency x term x age band rows;
  ``n_scen`` = yield-shock scenarios. Arrays are ``(n_scen, n_cell)`` and time
  series stack to ``(n_years + 1, n_scen, n_cell)``.
- Missing data: NaN in arrays, with the reason in the cell's ``missing`` list.
- Precision: float64.
"""
