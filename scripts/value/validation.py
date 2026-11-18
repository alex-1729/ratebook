"""Pure checks of the projection against published figures. No I/O."""

from __future__ import annotations

import numpy as np

from . import projection as pj


def rounding_tolerance_load(term_years, rate_step_pa=0.0001):
    """Largest implied-load error from rates published to ``rate_step_pa`` (half a step on each of two rates).

    dL/dnet ~ n (1-L)/(1+net), so a half-step on both the crediting rate and the
    net yield moves L by at most about n x rate_step. Shape broadcasts.
    """
    return term_years * rate_step_pa


def net_yield_reproduction(crediting_rate_pa, published_net_pa, term_years, truncation_pa=0.0001):
    """Does (1 + r)^n, i.e. a zero-load roll-up, reproduce a published 実質利回り?

    The insurer truncates the net yield to 2 decimals (TDF note), so a match is
    0 <= model - published < truncation step (plus float noise).
    Returns (model_net_pa, difference_pa, matches) arrays of shape (n,).
    """
    ratio = (1.0 + crediting_rate_pa) ** term_years
    model = pj.annualised_yield(ratio, term_years)
    diff = model - published_net_pa
    return model, diff, (diff > -1e-12) & (diff < truncation_pa + 1e-12)


def load_regimes(dates, loads, tolerance):
    """Split a dated implied-load series into regimes of (near-)constant load.

    A new regime starts when a value is more than ``tolerance`` away from the
    current regime's first value. Returns a list of dicts. Pure; inputs are
    sequences of equal length sorted by date.
    """
    regimes = []
    for d, x in zip(dates, loads):
        if regimes and abs(x - regimes[-1]["anchor"]) <= tolerance:
            r = regimes[-1]
            r["end"], r["n"] = d, r["n"] + 1
            r["min"], r["max"] = min(r["min"], x), max(r["max"], x)
            r["sum"] += x
        else:
            regimes.append({"start": d, "end": d, "n": 1, "anchor": x, "min": x, "max": x, "sum": x})
    return [
        {"start": r["start"], "end": r["end"], "n": r["n"], "mean": r["sum"] / r["n"],
         "min": r["min"], "max": r["max"]}
        for r in regimes
    ]


def implied_load_series(crediting_pa, net_pa, term_years):
    """Vectorised implied load over a history. Arrays (n_dates,)."""
    return pj.implied_initial_load(np.asarray(crediting_pa), np.asarray(net_pa), term_years)


def net_yield_from_charge(crediting_rate_pa, initial_charge, term_years):
    """Net yield a buyer earns from an up-front charge taken from a brochure, not from the net yield itself.

    AV0 = 1 - L rolls at the crediting rate for n years: ((1+r)^n (1-L))^(1/n) - 1. Shapes broadcast.
    """
    return pj.annualised_yield((1.0 + crediting_rate_pa) ** term_years * (1.0 - initial_charge), term_years)


def published_step(value_text):
    """Smallest step of a published percentage, as a fraction: '2.310' -> 0.00001, '4.4' -> 0.001."""
    decimals = len(value_text.split(".")[1]) if "." in value_text else 0
    return 10.0 ** -decimals / 100.0


def net_yield_check(crediting_rate_pa, initial_charge, term_years, published_net_pa, step_pa):
    """Compare a published 実質利回り with the one an independent charge gives.

    A match is what truncating (DFL: 0 <= diff < one step) or rounding (|diff| <= half a step) the
    model figure to the published precision would give: -step/2 <= diff < step.
    Returns (model_net_pa, difference_pa, matches).
    """
    model = net_yield_from_charge(crediting_rate_pa, initial_charge, term_years)
    diff = model - published_net_pa
    return model, diff, (diff >= -step_pa / 2 - 1e-12) & (diff < step_pa + 1e-12)
