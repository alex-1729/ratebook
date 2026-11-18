"""Pure projection functions. No I/O, no globals, no mutation of inputs.

See the package docstring for period, timing, sign and shape conventions.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

class CellParams(NamedTuple):
    """Per-cell product parameters. Every array has shape ``(n_cell,)``.

    crediting_rate_pa  積立利率 as a decimal, fixed for the term.
    initial_load       Fraction of premium not credited at issue (NaN if unknown).
    term_years         Accumulation term (int).
    pays_interest      1.0 if each year's interest is paid out (定期支払コース), else 0.0.
    """

    crediting_rate_pa: np.ndarray
    initial_load: np.ndarray
    term_years: np.ndarray
    pays_interest: np.ndarray


class State(NamedTuple):
    """Carried between policy years. Shapes ``(n_scen, n_cell)``."""

    account_value: np.ndarray
    cum_payout: np.ndarray


class StepOutputs(NamedTuple):
    """One policy year's outputs. Shapes ``(n_scen, n_cell)``."""

    account_value_eop: np.ndarray
    interest_credited: np.ndarray
    payout_eop: np.ndarray
    cum_payout_eop: np.ndarray


class Projection(NamedTuple):
    """Stacked outputs, shape ``(n_years + 1, n_scen, n_cell)``; index 0 is t = 0 (bop)."""

    account_value: np.ndarray
    payout: np.ndarray
    cum_payout: np.ndarray


# --------------------------------------------------------------------------
# One function per quantity
# --------------------------------------------------------------------------

def account_value_at_issue(premium, initial_load):
    """積立金 at t = 0: premium less the initial load.

    premium: scalar or (n_cell,); initial_load: (n_cell,). Returns (n_cell,).
    """
    return premium * (1.0 - initial_load)


def in_accumulation(t, term_years):
    """1.0 while policy year ``t`` (1-based) is within the term, else 0.0. Shape (n_cell,)."""
    return (t <= term_years).astype(np.float64)


def interest_credited(account_value_bop, crediting_rate_pa, active):
    """Interest for the year: AV_bop x rate, only while in the term. Shape (n_scen, n_cell)."""
    return account_value_bop * crediting_rate_pa * active


def interest_payout(interest, pays_interest):
    """定期支払: the year's interest paid at the anniversary where the plan pays it out."""
    return interest * pays_interest


def step(state: State, t: int, params: CellParams) -> tuple[State, StepOutputs]:
    """Roll one policy year: credit interest, pay it out if the plan does.

    After the term the account value is frozen (the maturity value is paid).
    """
    active = in_accumulation(t, params.term_years)
    interest = interest_credited(state.account_value, params.crediting_rate_pa, active)
    payout = interest_payout(interest, params.pays_interest)
    av_eop = state.account_value + interest - payout
    cum = state.cum_payout + payout
    return State(av_eop, cum), StepOutputs(av_eop, interest, payout, cum)


def project(params: CellParams, n_scen: int, n_years: int, premium: float = 1.0) -> Projection:
    """Driver: iterate ``step`` over policy years 1..n_years and stack outputs.

    Contains no product logic. Returns arrays of shape (n_years + 1, n_scen, n_cell).
    """
    n_cell = params.crediting_rate_pa.shape[0]
    av0 = np.broadcast_to(account_value_at_issue(premium, params.initial_load), (n_scen, n_cell))
    zeros = np.zeros((n_scen, n_cell))
    state = State(np.array(av0, dtype=np.float64), zeros)
    avs, pays, cums = [state.account_value], [zeros], [zeros]
    for t in range(1, n_years + 1):
        state, out = step(state, t, params)
        avs.append(out.account_value_eop)
        pays.append(out.payout_eop)
        cums.append(out.cum_payout_eop)
    return Projection(np.stack(avs), np.stack(pays), np.stack(cums))


def mva_factor(reference_rate_at_issue, reference_rate_now, years_remaining, spread=0.0):
    """Market value adjustment multiplier on the account value.

    Brochure form: deduction = AV x [1 - ((1+i)/(1+j+c))^(m/12)], so the adjusted
    value is AV x ((1+i)/(1+j+c))^(years remaining). c is the insurer's spread on
    the reference rate at surrender (MSP 0; NWL and DFL index plans 0.10%), so
    with c > 0 the factor is below 1 even when rates are unchanged.
    Broadcasts: i (n_cell,), j (n_scen, n_cell), years_remaining, spread (n_cell,).
    """
    return ((1.0 + reference_rate_at_issue) / (1.0 + reference_rate_now + spread)) ** np.maximum(years_remaining, 0.0)


def mva_effective_years(years_remaining, knee_months, slope_le, slope_gt, intercept_months):
    """Exponent of the MVA factor, in years, from the remaining term.

    Insurers shorten long durations: m months remaining counts as slope_le x m up
    to ``knee_months`` and slope_gt x m + intercept beyond it, e.g. DFL PCU3
    m <= 120: m, else 0.5m + 60; MetLife 0.81m, else 0.52(m + 67). A plain
    period factor k (NWL, T&D) is slope_le = slope_gt = k. Defaults
    (knee = inf, slope 1) give the remaining years unchanged. Shapes broadcast.
    """
    m = np.maximum(years_remaining, 0.0) * 12.0
    eff = np.where(m <= knee_months, slope_le * m, slope_gt * m + intercept_months)
    return eff / 12.0


def surrender_charge_amount(charge_pct_of_premium, premium):
    """解約控除: a percentage of the single premium (decimal in, amount out)."""
    return charge_pct_of_premium * premium


def surrender_value(account_value, mva_mult, charge_amount, cap):
    """解約返戻金 = min(AV x MVA, cap) - charge, floored at 0.

    cap is +inf where the plan has no cap (MSP 年金原資充実コース caps at premium).
    All arguments broadcast to (n_scen, n_cell).
    """
    return np.maximum(np.minimum(account_value * mva_mult, cap) - charge_amount, 0.0)


def return_ratio(value, cum_payout, premium=1.0):
    """返戻率: (value received now + payouts already received) / premium. Undiscounted."""
    return (value + cum_payout) / premium


def cumulative_fixed_payout(payout_rate_pa, years):
    """Sum of a level annual payout quoted as a % of premium (定期支払率), as a ratio."""
    return payout_rate_pa * years


def implied_initial_load(crediting_rate_pa, net_yield_pa, term_years):
    """Load implied by a published 実質利回り: 1 - ((1+net)/(1+cr))^n.

    If AV0 = P(1-L) rolls at cr to P(1+net)^n then L follows. Shapes broadcast.
    """
    return 1.0 - ((1.0 + net_yield_pa) / (1.0 + crediting_rate_pa)) ** term_years


def annualised_yield(ratio, years):
    """Annual compound yield implied by a single maturity ratio: ratio^(1/n) - 1."""
    return ratio ** (1.0 / years) - 1.0


def npv(cashflows, rate):
    """Present value of annual cashflows. cashflows (T+1, n); rate (n,). Returns (n,)."""
    t = np.arange(cashflows.shape[0], dtype=np.float64)[:, None]
    return np.sum(cashflows / (1.0 + rate) ** t, axis=0)


def irr(cashflows, lo=-0.99, hi=1.0, iterations=200):
    """Annual IRR by bisection, vectorised over columns. cashflows (T+1, n).

    Assumes one sign change (premium out, receipts in); NaN columns stay NaN.
    """
    n = cashflows.shape[1]
    lo_v, hi_v = np.full(n, lo), np.full(n, hi)
    f_lo = npv(cashflows, lo_v)
    for _ in range(iterations):
        mid = 0.5 * (lo_v + hi_v)
        f_mid = npv(cashflows, mid)
        same = np.sign(f_mid) == np.sign(f_lo)
        lo_v = np.where(same, mid, lo_v)
        f_lo = np.where(same, f_mid, f_lo)
        hi_v = np.where(same, hi_v, mid)
    out = 0.5 * (lo_v + hi_v)
    return np.where(np.any(np.isnan(cashflows), axis=0), np.nan, out)


def buyer_cashflows(payout, account_value_at_term, term_years, n_years, premium=1.0):
    """Buyer cashflows held to maturity: -premium at 0, payouts, AV at the term.

    payout (n_years+1, n_cell); account_value_at_term (n_cell,); returns (n_years+1, n_cell).
    """
    t = np.arange(n_years + 1)[:, None]
    maturity = np.where(t == term_years[None, :], account_value_at_term[None, :], 0.0)
    cf = payout + maturity
    return np.where(t == 0, -premium, cf)


def yen_entry_rate(spot_ttm, spread_in_yen):
    """Yen per unit paid when converting the yen premium (TTM + spread)."""
    return spot_ttm + spread_in_yen


def yen_exit_rate(spot_ttm, fx_move, spread_out_yen):
    """Yen per unit received at exit: TTM moved by ``fx_move`` (+0.1 = FC 10% stronger), less spread."""
    return spot_ttm * (1.0 + fx_move) - spread_out_yen


def yen_return_ratio(fc_return_ratio, spot_ttm, fx_move, spread_in_yen, spread_out_yen):
    """Yen received per yen paid, when all receipts convert at the same exit rate."""
    return fc_return_ratio * yen_exit_rate(spot_ttm, fx_move, spread_out_yen) / yen_entry_rate(spot_ttm, spread_in_yen)


def breakeven_fx_move(fc_return_ratio, spot_ttm, spread_in_yen, spread_out_yen, target_yen_ratio=1.0):
    """FX move (vs entry TTM) at which the yen return ratio equals ``target_yen_ratio``.

    Solve R x (S(1+x) - so) / (S + si) = T  ->  x = (T (S + si)/R + so)/S - 1.
    T = 1 is the yen break-even; T = a yen product's 返戻率 is the move at which
    the FX product only matches that yen product. Negative means the foreign
    currency can weaken by that much.
    """
    return (target_yen_ratio * (spot_ttm + spread_in_yen) / fc_return_ratio + spread_out_yen) / spot_ttm - 1.0


def fx_round_trip_cost(spot_ttm, spread_in_yen, spread_out_yen):
    """Fraction of value lost to the spreads at unchanged TTM: 1 - (S - so)/(S + si)."""
    return 1.0 - (spot_ttm - spread_out_yen) / (spot_ttm + spread_in_yen)
