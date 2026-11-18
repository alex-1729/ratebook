# Like-for-like value

What a buyer gets back from the single-premium savings products among all tracked and candidate products (39 in total), rather than its headline 積立利率. Rates are the latest published period (16 November 2026 for most insurers; MS Primary's MAREⅢ, Ashita no Yorokobi 2 and Broadway World III the window from 9 November). Outputs are per unit of single premium, in the contract currency unless marked yen.

Produced by `scripts/value/` (pure NumPy projection, tests in `scripts/value/tests/`). Which plans are modelled, with every charge, MVA rule and FX spread and the brochure page it came from, is in `scripts/value/model_spec.json`. Full per-cell output (surrender paths, shocked paths, every missing attribute) is in [like_for_like_value.json](like_for_like_value.json).

```
cd scripts && ../.venv/bin/python -m value.run                                   # rewrites reports/like_for_like_value.json
cd scripts && ../.venv/bin/python -m value.run --tables /tmp/tables.md           # also writes the tables below
cd scripts && ../.venv/bin/python -m unittest discover -s value/tests -t .         # 59 tests
```

## Coverage

All 39 products in `data/products` are accounted for: 19 are modelled (393 rate cells, every one with a complete maturity 返戻率 and surrender path) and 20 are listed under `not_modelled` with a reason.

| Modelled | Plans |
|---|---|
| DFL Premier Currency 3, and its Rakuten edition | Basic, maturity-focused |
| DFL Premier Receive 2, 3 | Periodic payment (basic), periodic accumulation |
| DFL Premier Partner | Fixed annuity, deferral 5–6 and 7–10 years (deferral account only) |
| MSP MAREⅢ, Ashita no Yorokobi 2, Broadway World III | Accumulation, periodic payment, annuity-fund/maturity-focused (100%, 70%, 50% death cover) |
| SML Tanoshimi Global 3 (fixed), Fullheart J-Road Global 3 | No target, target set; Fullheart on its published 15-year net yield |
| T&D Five Ten World 4, Yasashii Yen Nenkin 2, Premium World 6 | Yen annuity-focused, yen care, FX-linked with and without rider; PW6 yen and FX |
| Nippon Life Long Dream GOLD 3 | Grow (with and without rider), receive (fixed FX payout) |
| NWL Yuyu Jikan Advance 2, designated-currency annuity form | Tontine, USD fixed-term annuity deferral; designated-currency deferred annuity (5/10-year deferral) |
| MetLife Sunny Garden Prime | Account growth and periodic payment, with and without boost |
| ORIX Moonshot, PGF Hosho Sentaku Neo (USD) | Standard; basic and withdrawal |

| Not modelled | Why |
|---|---|
| DFL Journey, Road-M, Shisu Rendou Nenkin 2; SML TG3 index; NWL index forms (3); PGF Sodateru | Index-linked crediting |
| DFL Present 5, Story 5 | Costs inside the account value are not disclosed |
| MetLife Be With You Plus 2, Manulife Mirai Shushin, Meiji Yasuda FC Everybody Plus, NWL whole-life forms (5) | Whole life with cash values that are not a roll-up of a published rate |
| Credit Agricole Life じぶん年金介護プラス, NWL survival-benefit whole life | Benefit depends on care or annuity factors |
| Plans inside modelled products: annuity-payout forms (NWL AⅡ/確定/終身, MSP 受取/先取り, DFL Partner 終身 and index), DFL PR2 index, NLI fixed-yen payout, MSP Broadway target, MetLife no-reset | Value depends on annuity factors, an index or the FX path |

## Headline findings

1. **The brochures confirm the method.** DFL publishes its 契約時費用 in the Premier Currency 3 brochure: 3.5% / 4.1% (foreign currency, 5Y/10Y) and 1.5% / 2.2% (yen); Rakuten 3.1% / 3.5% and 1.3% / 1.9%. These match the charges the model had inferred from 実質利回り to within rounding, before the brochures were read. Charges are now taken from brochures for every insurer that publishes one.
2. **USD 10Y: the top ten are within 26bp a year.** Ranked by annual yield to the buyer, DFL Premier Receive 2/3 (5.08%) and T&D Premium World 6 FX (5.06%) lead, but both pay their return out every year, so their maturity 返戻率 is only about 151%. Among plans that compound, the leaders are DFL PCU3 Rakuten maturity-focused (4.97%), DFL Premier Partner's deferral account (4.94%), ORIX Moonshot (4.85%), DFL PCU3 maturity-focused (4.84%) and MS Primary's annuity-fund and maturity-focused courses (MAREⅢ, Ashita no Yorokobi 2, Broadway World III) and NWL's USD annuity deferral (4.82%).
3. **The headline rate still misleads most where there is an up-front charge.** NWL's USD fixed-term annuity shows 5.41% but its 5.5% charge brings the buyer to 4.82%; MS Primary's annuity-fund and maturity-focused courses fall from 5.36% to 4.82%; DFL PCU3's maturity-focused plan from 5.28% to 4.84%. ORIX (4.85%, no charge) beats all three.
4. **Yen 10Y: the leaders are deferred annuities.** NWL's designated-currency annuity (3.33% for premiums of 30 million yen or more, 3.17% below), DFL Premier Partner (3.29–3.33%), NWL's tontine (3.30%, reduced death benefit) and T&D's annuity-focused course (3.28%, survival-weighted) lead. Of these, NWL's designated-currency annuity and Partner have no up-front charge and pay a full death benefit, but their fund is paid out as an annuity. Among the rest the best are DFL PR2/PR3 (3.00%, paid out yearly) and T&D's care-cover course (2.87%, 132.7% at maturity).
5. **AUD 10Y is still DFL's market.** PCU3 Rakuten (5.23–5.28%) and the main edition (5.10–5.16%) lead after charges, then Nippon Life Long Dream (4.94%).
6. **Early surrender: charges matter less than rates.** At unchanged yields, USD 10Y surrender in year 1 returns 95.0% (DFL Partner, 5% charge, capped at premium) to 101.2% (DFL Receive, ages 86–90); most plans are 97.7–100.6%, T&D's FX plans 99.4–100.0% now that its MVA margin is applied (MSP Ashita's 70% and 50% death-cover courses pay a flat 70% or 50% until maturity). A 100bp rise in yields cuts year-1 values by about 8% on every plan, except MetLife (about 6.7%), whose MVA counts only 81% of the remaining months.
7. **FX spreads differ more than they did.** A yen round trip costs 0.63% of value in USD at TTM ± 0.50 (DFL, MSP, SML, MetLife, NLI), 0.32% at PGF (TTM + 0.50 / − 0.01), 0.13% at ORIX (TTM + 0.20 / − 0.01) and nothing at T&D, which converts at TTM. USD 10Y plans break even in yen if the dollar falls about 31–38% from 159.48 (USD/JPY about 99–110).

## Method

- **Account value.** 積立金 at issue = premium × (1 − initial charge), rolled up annually at the 積立利率 for the term. Interest-paying plans (定期支払, PR2/PR3, PW6, Long Dream receive, MetLife periodic) pay each year's interest out. DFL Premier Receive has no account: it pays the published 定期支払率 on a fixed base (the premium), which is the same calculation. No decrements: values are per surviving policy.
- **Initial charge.** In this order: a charge printed in the brochure or policy conditions (e.g. DFL maturity-focused, MSP annuity-fund courses, NWL USD annuity 5.5%; none for NWL's designated-currency annuity or PGF); otherwise implied from a published 実質利回り, L = 1 − ((1+net)/(1+r))^n (DFL PCU3 basic, T&D FTW4); otherwise a statement or published figure that the net yield equals the 積立利率. With none of these the 返戻率 is blank and flagged.
- **返戻率 at maturity** = (account value at the term + interest paid out) / premium, undiscounted. **Annual yield to buyer** is the IRR of premium out, payouts and maturity value in. The ranking key is that yield.
- **Surrender value** in policy year t = min(AV × MVA factor, cap) − charge, floored at 0. Charge = schedule × premium, or × account value where the insurer says so (T&D FTW4, MetLife, ORIX). MVA factor = ((1+i)/(1+j+c))^m with each insurer's margin c (DFL, NLI, NWL, MetLife, PGF 0.10%; T&D 0.05% yen, 0.10% FX) and its rule for m: DFL counts months beyond 120 at half (PCU3) or 0.6 (Receive), MetLife counts 0.81 of months up to 120 and 0.52 × (months + 67) beyond, NWL 0.8 of months from age 85, PGF 0.80 or 0.60 of months, T&D PW6 its 期間係数 (0.40–0.85 by age and sex for yen; 1.0 USD, 0.8 AUD). The cap is the premium where the plan caps surrender, or 70% / 50% for MSP Ashita's reduced-cover courses.
- **MVA reference rate.** The published 市場価格調整用利率 at issue where saved (DFL PCU3/Receive, SML), NWL's 基準利率, and for the current contract window the rates on the insurers' own pages, checked live on 17 November 2026: DFL Partner 市場価格調整用利率, ORIX 平均指標金利, MSP 指標金利, PGF 基準利率. T&D's yen plans use a reconstruction: T&D does not publish its 基準金利, but its 約款 names the index (JGB 5y/10y for Five Ten World 4 and Yasashii Yen Nenkin 2 by deferral; JGB 20y for PW6 ages 40-79, 10y for 80-90) and the averaging window, so the MOF par yields for 6, 9 and 10 November 2026 give 2.10% (5y), 2.72% (10y) and 3.61% (20y) for the 16 November window; flagged as reconstructed. Otherwise the 積立利率 is used as a proxy, flagged (MetLife, Nippon Life, T&D FX plans, whose indexes are proprietary bond indexes). NWL's 基準利率 now runs to the 16 November 2026 period on every form. Shocks are parallel ±100bp at surrender; impacts are relative to unchanged yields.
- **Yen basis.** Yen 返戻率 = 返戻率 × (TTM − spread out) / (TTM + spread in), TTM 159.48 (USD), 107.41 (AUD), 175.40 (EUR) and 87.73 (NZD) for 17 November 2026. Break-even FX move is the TTM change at which the yen 返戻率 is 100%; "ex spread" ignores the insurer's spread.
- **Gross bound.** (1+r)^n, or 1 + n·r for interest-paying plans: what the buyer would get with no charges.

## Ranking

Rank is among plans whose 返戻率 is complete, by annual yield to the buyer in the contract currency; ties share a rank. One row per product plan: where rates differ by age or sex the best band is shown, marked "best of N". "Missing" lists attributes not yet captured, including ones that affect only the surrender path or MVA.

Only 5- and 10-year terms are ranked here. Longer terms (yen 15/20Y, DFL Receive 3 20/30Y, T&D PW6 yen 15–30Y, SML Fullheart 15Y, MetLife 20Y, PGF Neo 15/20Y) are in the JSON.

#### JPY 5Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Missing |
|---|---|---|---|---|---|---|---|---|
| 1 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 15-40y payout [kihon_kakutei_pay25-40_def5-6] | all | 3.31% | 117.7% | 3.31% | 117.7% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 2 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 15-40y payout [kihon_kakutei_pay15-20_def5-6] | all | 3.29% | 117.6% | 3.29% | 117.6% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 3 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 10y payout | all | 2.67% | 114.1% | 2.67% | 114.1% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 4 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 100% death cover | all | 2.69% | 113.6% | 2.59% | 114.2% | none |
| 4 | MSP Broadway World III | Annuity-fund course (survival-weighted) | all | 2.69% | 113.6% | 2.59% | 114.2% | none |
| 6 | NWL Designated-currency individual annuity | JPY, premium 30m+ | 0-84 (best of 2) | 2.56% | 113.5% | 2.56% | 113.5% | none |
| 7 | NWL YY | Survival-weighted (tontine) | 0-84 (best of 2) | 2.53% | 113.3% | 2.53% | 113.3% | none |
| 8 | TDF FTW4 | JPY annuity-focused (survival-weighted) | all | 2.51% | 113.2% | 2.51% | 113.2% | MVA company margin |
| 8 | TDF Yasashii Yen Nenkin 2 (Easy Yen Annuity 2) | JPY annuity-focused (survival-weighted) | all | 2.51% | 113.2% | 2.51% | 113.2% | MVA company margin |
| 10 | NWL Designated-currency individual annuity | JPY, premium under 30m | 0-84 (best of 2) | 2.40% | 112.6% | 2.40% | 112.6% | none |
| 11 | DFL PCU3 (Rakuten) | Maturity-focused (Rakuten) | all | 2.65% | 112.5% | 2.38% | 114.0% | none |
| 12 | DFL PCU3 (Rakuten) | Basic (Rakuten) | all | 2.64% | 112.4% | 2.37% | 113.9% | none |
| 13 | DFL PCU3 | Maturity-focused (100% death benefit) | all | 2.63% | 112.2% | 2.32% | 113.9% | none |
| 14 | DFL PCU3 | Basic | all | 2.62% | 112.1% | 2.31% | 113.8% | none |
| 15 | SML TG3 | No target | all | 2.30% | 112.0% | 2.30% | 112.0% | none |
| 16 | MET Sunny Garden Prime | Account growth, boost | all | 2.17% | 111.3% | 2.17% | 111.3% | mva_reference_rate |
| 17 | TDF FTW4 | JPY care cover | all | 2.16% | 111.3% | 2.16% | 111.3% | MVA company margin |
| 17 | TDF Yasashii Yen Nenkin 2 (Easy Yen Annuity 2) | JPY care cover | all | 2.16% | 111.3% | 2.16% | 111.3% | MVA company margin |
| 17 | MET Sunny Garden Prime | Periodic payment, boost | all | 2.16% | 110.8% | 2.16% | 110.8% | mva_reference_rate |
| 20 | MET Sunny Garden Prime | Account growth | all | 2.02% | 110.5% | 2.02% | 110.5% | mva_reference_rate |
| 21 | MET Sunny Garden Prime | Periodic payment | all | 2.01% | 110.0% | 2.01% | 110.0% | mva_reference_rate |

#### JPY 10Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Missing |
|---|---|---|---|---|---|---|---|---|
| 1 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay25-40_def7-10] | all | 3.33% | 138.8% | 3.33% | 138.8% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 1 | NWL Designated-currency individual annuity | JPY, premium 30m+ | 0-84 (best of 2) | 3.33% | 138.8% | 3.33% | 138.8% | none |
| 3 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay15-20_def7-10] | all | 3.31% | 138.5% | 3.31% | 138.5% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 4 | NWL YY | Survival-weighted (tontine) | 0-84 (best of 2) | 3.30% | 138.4% | 3.30% | 138.4% | none |
| 5 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay10_def7-10] | all | 3.29% | 138.2% | 3.29% | 138.2% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 6 | TDF FTW4 | JPY annuity-focused (survival-weighted) | all | 3.28% | 138.1% | 3.28% | 138.1% | MVA company margin |
| 6 | TDF Yasashii Yen Nenkin 2 (Easy Yen Annuity 2) | JPY annuity-focused (survival-weighted) | all | 3.28% | 138.1% | 3.28% | 138.1% | MVA company margin |
| 8 | NWL Designated-currency individual annuity | JPY, premium under 30m | 0-84 (best of 2) | 3.17% | 136.6% | 3.17% | 136.6% | none |
| 9 | DFL PR2 | Periodic payment (basic) | 0-65 M (best of 10) | 3.00% | 130.0% | 3.00% | 130.0% | none |
| 9 | DFL PR3 | Periodic accumulation (standard) | 0-65 M (best of 10) | 3.00% | 130.0% | 3.00% | 130.0% | none |
| 11 | TDF FTW4 | JPY care cover | all | 2.87% | 132.7% | 2.87% | 132.7% | MVA company margin |
| 11 | TDF Yasashii Yen Nenkin 2 (Easy Yen Annuity 2) | JPY care cover | all | 2.87% | 132.7% | 2.87% | 132.7% | MVA company margin |
| 13 | MSP MARE3 | Accumulation course | all | 2.75% | 131.2% | 2.75% | 131.2% | none |
| 13 | MET Sunny Garden Prime | Account growth, boost | all | 2.75% | 131.2% | 2.75% | 131.2% | mva_reference_rate |
| 15 | MET Sunny Garden Prime | Periodic payment, boost | all | 2.74% | 127.4% | 2.74% | 127.4% | mva_reference_rate |
| 16 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 100% death cover | all | 2.75% | 129.9% | 2.65% | 131.2% | none |
| 16 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 70% death cover | all | 2.75% | 129.9% | 2.65% | 131.2% | none |
| 16 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 50% death cover | all | 2.75% | 129.9% | 2.65% | 131.2% | none |
| 16 | MSP Broadway World III | Annuity-fund course (survival-weighted) | all | 2.75% | 129.9% | 2.65% | 131.2% | none |
| 20 | MSP MARE3 | Periodic-payment course | all | 2.64% | 126.4% | 2.64% | 126.4% | none |
| 21 | MET Sunny Garden Prime | Account growth | all | 2.60% | 129.3% | 2.60% | 129.3% | mva_reference_rate |
| 22 | MET Sunny Garden Prime | Periodic payment | all | 2.59% | 125.9% | 2.59% | 125.9% | mva_reference_rate |
| 23 | ORX Moonshot single-premium whole life | Standard | all | 2.55% | 128.6% | 2.55% | 128.6% | none |
| 24 | DFL PCU3 (Rakuten) | Maturity-focused (Rakuten) | all | 2.69% | 127.9% | 2.49% | 130.4% | none |
| 25 | DFL PCU3 (Rakuten) | Basic (Rakuten) | all | 2.67% | 127.7% | 2.47% | 130.1% | none |
| 26 | DFL PCU3 | Maturity-focused (100% death benefit) | all | 2.67% | 127.3% | 2.44% | 130.1% | none |
| 27 | DFL PCU3 | Basic | all | 2.65% | 127.0% | 2.42% | 129.9% | none |
| 28 | SML TG3 | No target | all | 2.40% | 126.8% | 2.40% | 126.8% | none |

#### USD 5Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Yen 返戻率, flat TTM | Break-even FX move | Break-even ex spread | Missing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 15-40y payout [kihon_kakutei_pay15-20_def5-6] | all | 4.94% | 127.3% | 4.94% | 127.3% | 126.5% | -20.9% | -21.4% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 2 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 15-40y payout [kihon_kakutei_pay25-40_def5-6] | all | 4.84% | 126.7% | 4.84% | 126.7% | 125.9% | -20.5% | -21.0% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 3 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 10y payout | all | 4.64% | 125.5% | 4.64% | 125.5% | 124.7% | -19.7% | -20.3% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 4 | DFL PCU3 (Rakuten) | Maturity-focused (Rakuten) | all | 5.14% | 124.5% | 4.48% | 128.5% | 123.7% | -19.1% | -19.7% | none |
| 5 | DFL PCU3 (Rakuten) | Basic (Rakuten) | all | 5.11% | 124.3% | 4.45% | 128.3% | 123.5% | -19.0% | -19.6% | none |
| 6 | SML TG3 | No target | all | 4.40% | 124.0% | 4.40% | 124.0% | 123.2% | -18.8% | -19.4% | none |
| 6 | MET Sunny Garden Prime | Account growth, boost | all | 4.40% | 124.0% | 4.40% | 124.0% | 123.2% | -18.8% | -19.4% | mva_reference_rate |
| 8 | MET Sunny Garden Prime | Periodic payment, boost | all | 4.39% | 122.0% | 4.39% | 122.0% | 121.2% | -17.4% | -18.0% | mva_reference_rate |
| 9 | DFL PCU3 | Maturity-focused (100% death benefit) | all | 5.11% | 123.8% | 4.36% | 128.3% | 123.0% | -18.7% | -19.2% | none |
| 10 | SML TG3 | Target set | all | 4.35% | 123.7% | 4.35% | 123.7% | 123.0% | -18.6% | -19.2% | none |
| 11 | DFL PCU3 | Basic | all | 5.08% | 123.6% | 4.33% | 128.1% | 122.8% | -18.5% | -19.1% | none |
| 12 | MET Sunny Garden Prime | Account growth | all | 4.25% | 123.1% | 4.25% | 123.1% | 122.4% | -18.2% | -18.8% | mva_reference_rate |
| 13 | MET Sunny Garden Prime | Periodic payment | all | 4.24% | 121.2% | 4.24% | 121.2% | 120.4% | -16.9% | -17.5% | mva_reference_rate |
| 14 | NWL YY | USD fixed-term annuity, 20y payout | 0-69 (best of 3) | 5.40% | 122.9% | 4.21% | 130.1% | 122.5% | -18.4% | -18.6% | none |
| 15 | NWL YY | USD fixed-term annuity, 10y payout | 0-69 (best of 3) | 5.22% | 121.9% | 4.04% | 129.0% | 121.5% | -17.7% | -18.0% | none |
| 16 | NWL YY | USD fixed-term annuity, 5y payout | 0-69 (best of 3) | 5.13% | 121.4% | 3.95% | 128.4% | 121.0% | -17.3% | -17.6% | none |
| 17 | MSP MARE3 | Annuity-fund course (survival-weighted) | all | 4.57% | 121.3% | 3.93% | 125.0% | 120.5% | -17.0% | -17.5% | none |
| 17 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 100% death cover | all | 4.57% | 121.3% | 3.93% | 125.0% | 120.5% | -17.0% | -17.5% | none |
| 17 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 70% death cover | all | 4.57% | 121.3% | 3.93% | 125.0% | 120.5% | -17.0% | -17.5% | none |
| 17 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 50% death cover | all | 4.57% | 121.3% | 3.93% | 125.0% | 120.5% | -17.0% | -17.5% | none |
| 17 | MSP Broadway World III | Annuity-fund course (survival-weighted) | all | 4.57% | 121.3% | 3.93% | 125.0% | 120.5% | -17.0% | -17.5% | none |
| 22 | TDF FTW4 | FX-linked, no guarantee rider | all | 3.89% | 121.0% | 3.89% | 121.0% | 121.0% | -17.4% | -17.4% | mva_reference_rate, MVA company margin |
| 23 | NWL YY | Survival-weighted (tontine) | 0-84 (best of 2) | 3.84% | 120.7% | 3.84% | 120.7% | 120.4% | -16.9% | -17.2% | none |
| 23 | NWL Designated-currency individual annuity | All premiums | 0-84 (best of 2) | 3.84% | 120.7% | 3.84% | 120.7% | 120.4% | -16.9% | -17.2% | none |
| 25 | MSP MARE3 | Accumulation course | all | 3.80% | 120.5% | 3.80% | 120.5% | 119.7% | -16.4% | -17.0% | none |
| 25 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Accumulation course | all | 3.80% | 120.5% | 3.80% | 120.5% | 119.7% | -16.4% | -17.0% | none |
| 27 | TDF FTW4 | FX-linked, guarantee rider | all | 3.65% | 119.6% | 3.65% | 119.6% | 119.6% | -16.4% | -16.4% | mva_reference_rate, MVA company margin |

#### USD 10Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Yen 返戻率, flat TTM | Break-even FX move | Break-even ex spread | Missing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | DFL PR2 | Periodic payment (basic) | 0-65 M (best of 10) | 5.08% | 150.8% | 5.08% | 150.8% | 149.9% | -33.2% | -33.7% | none |
| 1 | DFL PR3 | Periodic accumulation (standard) | 0-65 M (best of 10) | 5.08% | 150.8% | 5.08% | 150.8% | 149.9% | -33.2% | -33.7% | none |
| 3 | TDF Lifetime Premium World 6 (without target-value function) | FX-linked, no rider | 40-69 F (best of 8) | 5.06% | 150.6% | 5.06% | 150.6% | 150.6% | -33.6% | -33.6% | mva_reference_rate, MVA company margin |
| 4 | DFL PCU3 (Rakuten) | Maturity-focused (Rakuten) | all | 5.34% | 162.4% | 4.97% | 168.2% | 161.3% | -37.9% | -38.4% | none |
| 5 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay10_def7-10] | all | 4.94% | 162.0% | 4.94% | 162.0% | 160.9% | -37.7% | -38.3% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 6 | ORX Moonshot single-premium whole life | Standard | all | 4.85% | 160.6% | 4.85% | 160.6% | 160.4% | -37.6% | -37.7% | none |
| 7 | DFL PCU3 | Maturity-focused (100% death benefit) | all | 5.28% | 160.4% | 4.84% | 167.3% | 159.4% | -37.2% | -37.7% | none |
| 8 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay15-20_def7-10] | all | 4.84% | 160.4% | 4.84% | 160.4% | 159.4% | -37.2% | -37.7% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 9 | MSP MARE3 | Annuity-fund course (survival-weighted) | all | 5.36% | 160.1% | 4.82% | 168.6% | 159.1% | -37.0% | -37.6% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 100% death cover | all | 5.36% | 160.1% | 4.82% | 168.6% | 159.1% | -37.0% | -37.6% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 70% death cover | all | 5.36% | 160.1% | 4.82% | 168.6% | 159.1% | -37.0% | -37.6% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 50% death cover | all | 5.36% | 160.1% | 4.82% | 168.6% | 159.1% | -37.0% | -37.6% | none |
| 9 | MSP Broadway World III | Annuity-fund course (survival-weighted) | all | 5.36% | 160.1% | 4.82% | 168.6% | 159.1% | -37.0% | -37.6% | none |
| 14 | NWL YY | USD fixed-term annuity, 10y payout | 0-69 (best of 3) | 5.41% | 160.0% | 4.82% | 169.4% | 159.5% | -37.3% | -37.5% | none |
| 14 | NWL YY | USD fixed-term annuity, 20y payout | 70-79 (best of 3) | 5.41% | 160.0% | 4.82% | 169.4% | 159.5% | -37.3% | -37.5% | none |
| 16 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay25-40_def7-10] | all | 4.81% | 160.0% | 4.81% | 160.0% | 159.0% | -37.0% | -37.5% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 16 | MET Sunny Garden Prime | Account growth, boost | all | 4.81% | 160.0% | 4.81% | 160.0% | 159.0% | -37.0% | -37.5% | mva_reference_rate |
| 18 | SML TG3 | No target | all | 4.80% | 159.8% | 4.80% | 159.8% | 158.8% | -36.9% | -37.4% | none |
| 18 | MET Sunny Garden Prime | Periodic payment, boost | all | 4.80% | 148.0% | 4.80% | 148.0% | 147.1% | -31.9% | -32.4% | mva_reference_rate |
| 20 | MSP MARE3 | Accumulation course | all | 4.79% | 159.7% | 4.79% | 159.7% | 158.7% | -36.9% | -37.4% | none |
| 20 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Accumulation course | all | 4.79% | 159.7% | 4.79% | 159.7% | 158.7% | -36.9% | -37.4% | none |
| 22 | TDF Lifetime Premium World 6 (without target-value function) | FX-linked, rider | 40-69 F (best of 8) | 4.74% | 147.4% | 4.74% | 147.4% | 147.4% | -32.2% | -32.2% | mva_reference_rate, MVA company margin |
| 23 | MSP MARE3 | Periodic-payment course | all | 4.67% | 146.7% | 4.67% | 146.7% | 145.8% | -31.3% | -31.8% | none |
| 23 | MSP Broadway World III | Periodic-payment course | all | 4.67% | 146.7% | 4.67% | 146.7% | 145.8% | -31.3% | -31.8% | none |
| 25 | MET Sunny Garden Prime | Account growth | all | 4.66% | 157.7% | 4.66% | 157.7% | 156.7% | -36.1% | -36.6% | mva_reference_rate |
| 26 | MET Sunny Garden Prime | Periodic payment | all | 4.65% | 146.5% | 4.65% | 146.5% | 145.6% | -31.2% | -31.7% | mva_reference_rate |
| 27 | NLI Long Dream GOLD 3 | Receive course, fixed FX payout | 15-75 (best of 2) | 4.64% | 146.4% | 4.64% | 146.4% | 145.5% | -31.2% | -31.7% | mva_reference_rate |
| 28 | NLI Long Dream GOLD 3 | Grow course, no rider | 15-75 (best of 2) | 4.64% | 157.4% | 4.64% | 157.4% | 156.4% | -36.0% | -36.5% | mva_reference_rate |
| 29 | NWL YY | USD fixed-term annuity, 5y payout | 0-69 (best of 3) | 5.23% | 157.3% | 4.64% | 166.5% | 156.8% | -36.2% | -36.4% | none |
| 30 | DFL PCU3 (Rakuten) | Basic (Rakuten) | all | 5.01% | 157.2% | 4.63% | 163.0% | 156.3% | -35.9% | -36.4% | none |
| 31 | TDF FTW4 | FX-linked, no guarantee rider | all | 4.56% | 156.2% | 4.56% | 156.2% | 156.2% | -36.0% | -36.0% | mva_reference_rate, MVA company margin |
| 32 | NLI Long Dream GOLD 3 | Grow course, with rider | 15-75 (best of 2) | 4.53% | 155.7% | 4.53% | 155.7% | 154.8% | -35.3% | -35.8% | mva_reference_rate |
| 33 | DFL PCU3 | Basic | all | 4.95% | 155.4% | 4.51% | 162.1% | 154.5% | -35.2% | -35.7% | none |
| 34 | SML TG3 | Target set | all | 4.50% | 155.3% | 4.50% | 155.3% | 154.3% | -35.1% | -35.6% | none |
| 35 | NWL YY | Survival-weighted (tontine) | 0-84 (best of 2) | 4.34% | 152.9% | 4.34% | 152.9% | 152.5% | -34.4% | -34.6% | none |
| 35 | NWL Designated-currency individual annuity | All premiums | 0-84 (best of 2) | 4.34% | 152.9% | 4.34% | 152.9% | 152.5% | -34.4% | -34.6% | none |
| 37 | TDF FTW4 | FX-linked, guarantee rider | all | 4.29% | 152.2% | 4.29% | 152.2% | 152.2% | -34.3% | -34.3% | mva_reference_rate, MVA company margin |

#### AUD 5Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Yen 返戻率, flat TTM | Break-even FX move | Break-even ex spread | Missing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | NWL YY | Survival-weighted (tontine) | 0-84 (best of 2) | 4.27% | 123.3% | 4.27% | 123.3% | 122.7% | -18.5% | -18.9% | none |
| 1 | NWL Designated-currency individual annuity | All premiums | 0-84 (best of 2) | 4.27% | 123.3% | 4.27% | 123.3% | 122.7% | -18.5% | -18.9% | none |
| 3 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 15-40y payout [kihon_kakutei_pay15-20_def5-6] | all | 4.18% | 122.7% | 4.18% | 122.7% | 121.6% | -17.7% | -18.5% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 4 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 10y payout | all | 4.17% | 122.7% | 4.17% | 122.7% | 121.5% | -17.6% | -18.5% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 5 | DFL PCU3 (Rakuten) | Maturity-focused (Rakuten) | all | 4.81% | 122.6% | 4.15% | 126.5% | 121.4% | -17.6% | -18.4% | none |
| 6 | DFL Premier Partner | Fixed annuity, deferral 5-6y (modelled 5y), 15-40y payout [kihon_kakutei_pay25-40_def5-6] | all | 4.14% | 122.5% | 4.14% | 122.5% | 121.4% | -17.5% | -18.4% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 7 | DFL PCU3 (Rakuten) | Basic (Rakuten) | all | 4.76% | 122.3% | 4.10% | 126.2% | 121.1% | -17.4% | -18.2% | none |
| 8 | DFL PCU3 | Maturity-focused (100% death benefit) | all | 4.78% | 121.9% | 4.04% | 126.3% | 120.7% | -17.1% | -17.9% | none |
| 9 | MSP MARE3 | Annuity-fund course (survival-weighted) | all | 4.64% | 121.7% | 4.00% | 125.5% | 120.6% | -17.0% | -17.8% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 100% death cover | all | 4.64% | 121.7% | 4.00% | 125.5% | 120.6% | -17.0% | -17.8% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 70% death cover | all | 4.64% | 121.7% | 4.00% | 125.5% | 120.6% | -17.0% | -17.8% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 50% death cover | all | 4.64% | 121.7% | 4.00% | 125.5% | 120.6% | -17.0% | -17.8% | none |
| 9 | MSP Broadway World III | Annuity-fund course (survival-weighted) | all | 4.64% | 121.7% | 4.00% | 125.5% | 120.6% | -17.0% | -17.8% | none |
| 14 | DFL PCU3 | Basic | all | 4.73% | 121.5% | 3.98% | 126.0% | 120.4% | -16.9% | -17.7% | none |
| 15 | MSP MARE3 | Accumulation course | all | 3.94% | 121.3% | 3.94% | 121.3% | 120.2% | -16.7% | -17.6% | none |
| 15 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Accumulation course | all | 3.94% | 121.3% | 3.94% | 121.3% | 120.2% | -16.7% | -17.6% | none |
| 17 | MSP MARE3 | Periodic-payment course | all | 3.82% | 119.1% | 3.82% | 119.1% | 118.0% | -15.2% | -16.0% | none |
| 17 | MSP Broadway World III | Periodic-payment course | all | 3.82% | 119.1% | 3.82% | 119.1% | 118.0% | -15.2% | -16.0% | none |
| 19 | TDF FTW4 | FX-linked, no guarantee rider | all | 3.72% | 120.0% | 3.71% | 120.0% | 120.0% | -16.7% | -16.7% | mva_reference_rate, MVA company margin |
| 20 | SML TG3 | No target | all | 3.70% | 119.9% | 3.70% | 119.9% | 118.8% | -15.8% | -16.6% | none |
| 21 | TDF FTW4 | FX-linked, guarantee rider | all | 3.39% | 118.1% | 3.38% | 118.1% | 118.1% | -15.3% | -15.3% | mva_reference_rate, MVA company margin |
| 22 | SML TG3 | Target set | all | 2.95% | 115.6% | 2.95% | 115.6% | 114.6% | -12.7% | -13.5% | none |

#### AUD 10Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Yen 返戻率, flat TTM | Break-even FX move | Break-even ex spread | Missing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | DFL PCU3 (Rakuten) | Maturity-focused (Rakuten) | all | 5.66% | 167.4% | 5.28% | 173.4% | 165.8% | -39.5% | -40.2% | none |
| 2 | DFL PCU3 (Rakuten) | Basic (Rakuten) | all | 5.61% | 166.5% | 5.23% | 172.6% | 165.0% | -39.2% | -39.9% | none |
| 3 | DFL PCU3 | Maturity-focused (100% death benefit) | all | 5.60% | 165.4% | 5.16% | 172.4% | 163.8% | -38.8% | -39.5% | none |
| 4 | DFL PCU3 | Basic | all | 5.55% | 164.4% | 5.10% | 171.6% | 162.9% | -38.4% | -39.2% | none |
| 5 | NLI Long Dream GOLD 3 | Grow course, no rider | 15-75 (best of 2) | 4.94% | 162.0% | 4.94% | 162.0% | 160.5% | -37.5% | -38.3% | mva_reference_rate |
| 5 | NLI Long Dream GOLD 3 | Receive course, fixed FX payout | 15-75 (best of 2) | 4.94% | 149.4% | 4.94% | 149.4% | 148.0% | -32.3% | -33.1% | mva_reference_rate |
| 7 | DFL PR2 | Periodic payment (basic) | 0-65 M (best of 10) | 4.85% | 148.5% | 4.85% | 148.5% | 147.1% | -31.9% | -32.7% | none |
| 7 | DFL PR3 | Periodic accumulation (standard) | 0-65 M (best of 10) | 4.85% | 148.5% | 4.85% | 148.5% | 147.1% | -31.9% | -32.7% | none |
| 9 | MSP MARE3 | Annuity-fund course (survival-weighted) | all | 5.38% | 160.4% | 4.84% | 168.9% | 159.0% | -36.9% | -37.7% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 100% death cover | all | 5.38% | 160.4% | 4.84% | 168.9% | 159.0% | -36.9% | -37.7% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 70% death cover | all | 5.38% | 160.4% | 4.84% | 168.9% | 159.0% | -36.9% | -37.7% | none |
| 9 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 50% death cover | all | 5.38% | 160.4% | 4.84% | 168.9% | 159.0% | -36.9% | -37.7% | none |
| 9 | MSP Broadway World III | Annuity-fund course (survival-weighted) | all | 5.38% | 160.4% | 4.84% | 168.9% | 159.0% | -36.9% | -37.7% | none |
| 14 | MSP MARE3 | Accumulation course | all | 4.84% | 160.4% | 4.84% | 160.4% | 158.9% | -36.9% | -37.7% | none |
| 14 | MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Accumulation course | all | 4.84% | 160.4% | 4.84% | 160.4% | 158.9% | -36.9% | -37.7% | none |
| 16 | NLI Long Dream GOLD 3 | Grow course, with rider | 15-75 (best of 2) | 4.76% | 159.2% | 4.76% | 159.2% | 157.7% | -36.4% | -37.2% | mva_reference_rate |
| 17 | MSP MARE3 | Periodic-payment course | all | 4.72% | 147.2% | 4.72% | 147.2% | 145.8% | -31.3% | -32.1% | none |
| 17 | MSP Broadway World III | Periodic-payment course | all | 4.72% | 147.2% | 4.72% | 147.2% | 145.8% | -31.3% | -32.1% | none |
| 19 | NWL YY | Survival-weighted (tontine) | 0-84 (best of 2) | 4.71% | 158.4% | 4.71% | 158.4% | 157.7% | -36.6% | -36.9% | none |
| 19 | NWL Designated-currency individual annuity | All premiums | 0-84 (best of 2) | 4.71% | 158.4% | 4.71% | 158.4% | 157.7% | -36.6% | -36.9% | none |
| 21 | MET Sunny Garden Prime | Account growth, boost | all | 4.70% | 158.3% | 4.70% | 158.3% | 156.8% | -36.1% | -36.8% | mva_reference_rate |
| 22 | MET Sunny Garden Prime | Periodic payment, boost | all | 4.69% | 146.9% | 4.69% | 146.9% | 145.5% | -31.1% | -31.9% | mva_reference_rate |
| 23 | MET Sunny Garden Prime | Account growth | all | 4.55% | 156.0% | 4.55% | 156.0% | 154.6% | -35.2% | -35.9% | mva_reference_rate |
| 24 | MET Sunny Garden Prime | Periodic payment | all | 4.54% | 145.4% | 4.54% | 145.4% | 144.1% | -30.4% | -31.2% | mva_reference_rate |
| 25 | SML TG3 | No target | all | 4.40% | 153.8% | 4.40% | 153.8% | 152.4% | -34.2% | -35.0% | none |
| 26 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay25-40_def7-10] | all | 4.28% | 152.1% | 4.28% | 152.1% | 150.6% | -33.5% | -34.2% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 27 | TDF FTW4 | FX-linked, no guarantee rider | all | 4.25% | 151.6% | 4.25% | 151.6% | 151.6% | -34.0% | -34.0% | mva_reference_rate, MVA company margin |
| 28 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay10_def7-10] | all | 4.18% | 150.6% | 4.18% | 150.6% | 149.2% | -32.8% | -33.6% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 29 | DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay15-20_def7-10] | all | 4.14% | 150.0% | 4.14% | 150.0% | 148.6% | -32.6% | -33.3% | MVA duration runs to the last annuity payment; modelled to the end of deferral only |
| 30 | TDF FTW4 | FX-linked, guarantee rider | all | 3.72% | 144.1% | 3.72% | 144.1% | 144.1% | -30.6% | -30.6% | mva_reference_rate, MVA company margin |
| 31 | SML TG3 | Target set | all | 3.20% | 137.0% | 3.20% | 137.0% | 135.8% | -26.2% | -27.0% | none |

#### EUR 10Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Yen 返戻率, flat TTM | Break-even FX move | Break-even ex spread | Missing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MSP MARE3 | Accumulation course | all | 2.95% | 133.7% | 2.95% | 133.7% | 133.0% | -24.7% | -25.2% | none |
| 2 | MSP MARE3 | Periodic-payment course | all | 2.83% | 128.3% | 2.83% | 128.3% | 127.6% | -21.6% | -22.1% | none |

#### NZD 5Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Yen 返戻率, flat TTM | Break-even FX move | Break-even ex spread | Missing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MSP MARE3 | Accumulation course | all | 2.26% | 111.8% | 2.26% | 111.8% | 110.6% | -9.5% | -10.6% | none |
| 2 | MSP MARE3 | Periodic-payment course | all | 2.14% | 110.7% | 2.14% | 110.7% | 109.4% | -8.6% | -9.7% | none |
| 2 | MSP Broadway World III | Periodic-payment course | all | 2.14% | 110.7% | 2.14% | 110.7% | 109.4% | -8.6% | -9.7% | none |

#### NZD 10Y

| Rank | Product | Plan | Age | 積立利率 | 返戻率 at maturity | Annual yield to buyer | Gross (1+r)^n bound | Yen 返戻率, flat TTM | Break-even FX move | Break-even ex spread | Missing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MSP MARE3 | Accumulation course | all | 3.19% | 136.9% | 3.19% | 136.9% | 135.3% | -26.0% | -26.9% | none |
| 2 | MSP MARE3 | Periodic-payment course | all | 3.07% | 130.7% | 3.07% | 130.7% | 129.2% | -22.5% | -23.5% | none |
| 2 | MSP Broadway World III | Periodic-payment course | all | 3.07% | 130.7% | 3.07% | 130.7% | 129.2% | -22.5% | -23.5% | none |

## Surrender value path (10-year terms)

返戻率 on surrender just before each policy anniversary, at unchanged yields and with yields 100bp higher or lower at surrender. Where rates vary by age or sex, the best band is shown. Other terms and currencies are in the JSON.

| Product | Plan | Ccy | Term | Yr 1 | Yr 2 | Yr 3 | Yr 5 | Yr 1 at +100bp | Yr 1 at −100bp | Maturity |
|---|---|---|---|---|---|---|---|---|---|---|
| DFL PCU3 | Basic | JPY | 10 | 99.5% | 102.2% | 105.1% | 110.9% | 91.2% | 108.7% | 127.0% |
| DFL PCU3 | Basic | USD | 10 | 99.8% | 104.8% | 110.1% | 121.5% | 91.7% | 108.7% | 155.4% |
| DFL PCU3 | Maturity-focused (100% death benefit) | JPY | 10 | 99.5% | 100.0% | 100.0% | 100.0% | 91.2% | 100.0% | 127.3% |
| DFL PCU3 | Maturity-focused (100% death benefit) | USD | 10 | 100.0% | 100.0% | 100.0% | 100.0% | 92.0% | 100.0% | 160.4% |
| DFL PCU3 (Rakuten) | Basic (Rakuten) | JPY | 10 | 99.8% | 102.6% | 105.4% | 111.4% | 91.5% | 109.0% | 127.7% |
| DFL PCU3 (Rakuten) | Basic (Rakuten) | USD | 10 | 100.4% | 105.5% | 110.9% | 122.6% | 92.3% | 109.4% | 157.2% |
| DFL PCU3 (Rakuten) | Maturity-focused (Rakuten) | JPY | 10 | 99.9% | 100.0% | 100.0% | 100.0% | 91.5% | 100.0% | 127.9% |
| DFL PCU3 (Rakuten) | Maturity-focused (Rakuten) | USD | 10 | 100.0% | 100.0% | 100.0% | 100.0% | 92.6% | 100.0% | 162.4% |
| DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay10_def7-10] | JPY | 10 | 97.2% | 97.5% | 97.8% | 98.3% | 91.1% | 97.2% | 138.2% |
| DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay10_def7-10] | USD | 10 | 95.0% | 95.5% | 96.0% | 97.0% | 90.7% | 95.0% | 162.0% |
| DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay15-20_def7-10] | JPY | 10 | 97.2% | 97.5% | 97.8% | 98.3% | 91.1% | 97.2% | 138.5% |
| DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay15-20_def7-10] | USD | 10 | 95.0% | 95.5% | 96.0% | 97.0% | 90.6% | 95.0% | 160.4% |
| DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay25-40_def7-10] | JPY | 10 | 97.2% | 97.5% | 97.8% | 98.3% | 91.2% | 97.2% | 138.8% |
| DFL Premier Partner | Fixed annuity, deferral 7-10y (modelled 10y) [kihon_kakutei_pay25-40_def7-10] | USD | 10 | 95.0% | 95.5% | 96.0% | 97.0% | 90.6% | 95.0% | 160.0% |
| DFL PR2 | Periodic payment (basic) | JPY | 10 | 99.6% | 103.0% | 106.3% | 113.0% | 91.4% | 108.7% | 130.0% |
| DFL PR2 | Periodic payment (basic) | USD | 10 | 98.2% | 104.0% | 109.8% | 121.3% | 90.2% | 107.1% | 150.8% |
| DFL PR3 | Periodic accumulation (standard) | JPY | 10 | 98.0% | 101.5% | 105.0% | 112.1% | 89.9% | 107.0% | 130.0% |
| DFL PR3 | Periodic accumulation (standard) | USD | 10 | 98.2% | 104.0% | 109.8% | 121.3% | 90.2% | 107.0% | 150.8% |
| MET Sunny Garden Prime | Periodic payment | JPY | 10 | 98.9% | 101.9% | 104.8% | 110.8% | 92.1% | 106.2% | 125.9% |
| MET Sunny Garden Prime | Periodic payment | USD | 10 | 98.0% | 103.3% | 108.6% | 119.3% | 91.3% | 105.1% | 146.5% |
| MET Sunny Garden Prime | Periodic payment, boost | JPY | 10 | 99.0% | 102.2% | 105.3% | 111.5% | 92.3% | 106.4% | 127.4% |
| MET Sunny Garden Prime | Periodic payment, boost | USD | 10 | 98.1% | 103.6% | 109.1% | 120.0% | 91.5% | 105.3% | 148.0% |
| MET Sunny Garden Prime | Account growth | JPY | 10 | 98.8% | 101.8% | 104.8% | 111.2% | 91.8% | 106.3% | 129.3% |
| MET Sunny Garden Prime | Account growth | USD | 10 | 97.7% | 102.9% | 108.5% | 120.6% | 90.7% | 105.2% | 157.7% |
| MET Sunny Garden Prime | Account growth, boost | JPY | 10 | 98.9% | 102.1% | 105.3% | 112.0% | 92.0% | 106.5% | 131.2% |
| MET Sunny Garden Prime | Account growth, boost | USD | 10 | 97.8% | 103.2% | 109.0% | 121.4% | 90.8% | 105.3% | 160.0% |
| MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 100% death cover | JPY | 10 | 100.0% | 100.0% | 100.0% | 100.0% | 93.2% | 100.0% | 129.9% |
| MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 100% death cover | USD | 10 | 100.0% | 100.0% | 100.0% | 100.0% | 92.0% | 100.0% | 160.1% |
| MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 50% death cover | JPY | 10 | 50.0% | 50.0% | 50.0% | 50.0% | – | – | 129.9% |
| MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 50% death cover | USD | 10 | 50.0% | 50.0% | 50.0% | 50.0% | – | – | 160.1% |
| MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 70% death cover | JPY | 10 | 70.0% | 70.0% | 70.0% | 70.0% | – | – | 129.9% |
| MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Maturity-focused, 70% death cover | USD | 10 | 70.0% | 70.0% | 70.0% | 70.0% | – | – | 160.1% |
| MSP Ashita no Yorokobi 2 (Tomorrow's Joy 2) | Accumulation course | USD | 10 | 98.8% | 104.4% | 110.3% | 122.8% | 90.3% | 108.1% | 159.7% |
| MSP Broadway World III | Annuity-fund course (survival-weighted) | JPY | 10 | 100.0% | 100.0% | 100.0% | 100.0% | 93.2% | 100.0% | 129.9% |
| MSP Broadway World III | Annuity-fund course (survival-weighted) | USD | 10 | 100.0% | 100.0% | 100.0% | 100.0% | 92.0% | 100.0% | 160.1% |
| MSP Broadway World III | Periodic-payment course | USD | 10 | 98.7% | 103.9% | 109.2% | 119.8% | 90.5% | 107.6% | 146.7% |
| MSP MARE3 | Annuity-fund course (survival-weighted) | USD | 10 | 100.0% | 100.0% | 100.0% | 100.0% | 92.0% | 100.0% | 160.1% |
| MSP MARE3 | Periodic-payment course | JPY | 10 | 100.6% | 103.5% | 106.3% | 112.0% | 92.3% | 109.8% | 126.4% |
| MSP MARE3 | Periodic-payment course | USD | 10 | 98.7% | 103.9% | 109.2% | 119.8% | 90.5% | 107.6% | 146.7% |
| MSP MARE3 | Accumulation course | JPY | 10 | 100.8% | 103.8% | 106.9% | 113.3% | 92.2% | 110.2% | 131.2% |
| MSP MARE3 | Accumulation course | USD | 10 | 98.8% | 104.4% | 110.3% | 122.8% | 90.3% | 108.1% | 159.7% |
| NLI Long Dream GOLD 3 | Grow course, no rider | USD | 10 | 99.7% | 105.1% | 110.6% | 122.5% | 91.2% | 109.1% | 157.4% |
| NLI Long Dream GOLD 3 | Grow course, with rider | USD | 10 | 99.6% | 104.8% | 110.3% | 121.8% | 91.1% | 109.0% | 155.7% |
| NLI Long Dream GOLD 3 | Receive course, fixed FX payout | USD | 10 | 99.8% | 104.9% | 110.1% | 120.3% | 91.7% | 108.7% | 146.4% |
| NWL Designated-currency individual annuity | All premiums | USD | 10 | 99.0% | 104.0% | 109.2% | 120.4% | 90.6% | 108.2% | 152.9% |
| NWL Designated-currency individual annuity | JPY, premium 30m+ | JPY | 10 | 100.9% | 104.6% | 108.4% | 116.3% | 92.5% | 110.3% | 138.8% |
| NWL Designated-currency individual annuity | JPY, premium under 30m | JPY | 10 | 100.8% | 104.3% | 107.9% | 115.4% | 92.3% | 110.1% | 136.6% |
| NWL YY | Survival-weighted (tontine) | JPY | 10 | 100.9% | 104.5% | 108.3% | 116.2% | 92.4% | 110.2% | 138.4% |
| NWL YY | Survival-weighted (tontine) | USD | 10 | 99.0% | 104.0% | 109.2% | 120.4% | 90.6% | 108.2% | 152.9% |
| NWL YY | USD fixed-term annuity, 10y payout | USD | 10 | 98.8% | 104.2% | 109.9% | 122.4% | 90.7% | 107.6% | 160.0% |
| NWL YY | USD fixed-term annuity, 20y payout | USD | 10 | 98.8% | 104.2% | 109.9% | 122.4% | 90.7% | 107.6% | 160.0% |
| NWL YY | USD fixed-term annuity, 5y payout | USD | 10 | 98.6% | 103.8% | 109.4% | 121.4% | 90.5% | 107.5% | 157.3% |
| ORX Moonshot single-premium whole life | Standard | JPY | 10 | 99.5% | 102.3% | 105.3% | 111.4% | 91.0% | 108.8% | 128.6% |
| ORX Moonshot single-premium whole life | Standard | USD | 10 | 98.6% | 104.0% | 109.7% | 122.2% | 90.1% | 107.9% | 160.6% |
| SML TG3 | No target | JPY | 10 | 100.4% | 103.1% | 105.8% | 111.4% | 92.0% | 109.7% | 126.8% |
| SML TG3 | No target | USD | 10 | 99.8% | 105.3% | 111.1% | 123.4% | 91.3% | 109.1% | 159.8% |
| SML TG3 | Target set | USD | 10 | 99.5% | 104.7% | 110.1% | 121.6% | 91.0% | 108.8% | 155.3% |
| TDF FTW4 | FX-linked, no guarantee rider | USD | 10 | 100.0% | 105.1% | 110.4% | 121.8% | 91.5% | 109.4% | 156.2% |
| TDF FTW4 | FX-linked, guarantee rider | USD | 10 | 99.7% | 104.6% | 109.5% | 120.2% | 91.2% | 109.1% | 152.2% |
| TDF FTW4 | JPY care cover | JPY | 10 | 100.9% | 104.0% | 107.2% | 113.9% | 92.3% | 110.3% | 132.7% |
| TDF FTW4 | JPY annuity-focused (survival-weighted) | JPY | 10 | 98.5% | 98.6% | 98.7% | 98.9% | 92.7% | 98.5% | 138.1% |
| TDF Lifetime Premium World 6 (without target-value function) | FX-linked, no rider | USD | 10 | 99.7% | 105.4% | 110.9% | 122.1% | 91.6% | 108.6% | 150.6% |
| TDF Lifetime Premium World 6 (without target-value function) | FX-linked, rider | USD | 10 | 99.4% | 104.7% | 110.0% | 120.5% | 91.3% | 108.3% | 147.4% |
| TDF Yasashii Yen Nenkin 2 (Easy Yen Annuity 2) | JPY care cover | JPY | 10 | 100.9% | 104.0% | 107.2% | 113.9% | 92.3% | 110.3% | 132.7% |
| TDF Yasashii Yen Nenkin 2 (Easy Yen Annuity 2) | JPY annuity-focused (survival-weighted) | JPY | 10 | 98.5% | 98.6% | 98.7% | 98.9% | 92.7% | 98.5% | 138.1% |

## MVA impact

Change in year-1 and year-5 surrender value from a parallel ±100bp shock to the MVA reference rate, relative to unchanged yields. The effect depends mainly on how many years the formula counts, so it is near-identical across insurers for the same term; the exceptions come from each insurer's month rule.

| Term | Year 1: +100bp | Year 1: −100bp | Year 5: +100bp | Year 5: −100bp | Different |
|---|---|---|---|---|---|
| 5Y | −3.7% to −3.8% | +3.9% to +4.0% | – | – | MetLife −3.0% / +3.2% |
| 10Y | −8.1% to −8.4% | +8.8% to +9.3% | −4.6% to −4.8% | +4.8% to +5.0% | MetLife −6.7% / +7.2% (counts 81% of months); NWL annuity from age 85 −6.6% / +7.1% (80% of months) |
| 15–30Y | larger: about −11% to −13% at 15Y and −13% to −17% at 20Y in year 1 | | | | Smaller where months are scaled down: PGF Neo (×0.80 for 20 years, ×0.60 for 15: −13.3% / −7.6%), T&D PW6 by its 期間係数 (0.40–0.85 by age and sex for yen, 0.8 for AUD), DFL PCU3 beyond 120 months |

The "Status" column in the JSON says "complete" where the insurer's formula and published reference rate are both used: DFL (PCU3, Receive, Partner), SML, NWL, ORIX, MSP (all three) and PGF: 263 of the 367 cells that have an MVA. A further 24, T&D's yen plans, say "complete: reference rate at issue reconstructed from JGB par yields (T&D's own 基準金利 is unpublished)", and the MVA table marks their reference rate "reconstructed". It says "indicative" for the other 80, where the reference rate at issue is proxied by the 積立利率: MetLife, Nippon Life and T&D's FX plans. MetLife publishes the surrender-date index and its margin a on its product page, which refuses automated access, so neither was captured; Nippon Life's index is a proprietary Bloomberg BVAL average. With unchanged yields the proxy does not change the surrender path; it only shifts the ±100bp figures by a few tenths of a point.

## Validation

- **DFL brochure charges vs implied loads.** The 契約時費用 printed in the Premier Currency 3 brochure (3.5/4.1% foreign currency, 1.5/2.2% yen) and the Rakuten disclosure (3.1/3.5%, 1.3/1.9%) match the loads implied by the published 実質利回り within the rounding tolerance (tested).
- **T&D: (1+積立利率)^n reproduces the published 実質的な利回り** for all 16 current FTW4 cells within the insurer's 2-decimal truncation (tested).
- **DFL PCU3 load regimes.** The implied load is constant within each regime across all 88 half-month periods since April 2023, changing only on 2025-04-01 and 2026-04-01 (tested).
- **Insurer worked examples.** ORIX: 100,000 USD at 4.5% for 10 years → 155,296, with no charge (tested). MSP Ashita 積立コース brochure example: 3.9% for 10 years → 146,607 (tested). Nippon Life MVA table: at a 2.0% reference rate and 0.1% margin the deduction is 0.98% with 10 years left, 0.49% with 5 and 0.10% with 1 (tested). NWL designated-currency annuity (しおり 2026年7月版): 積立金 104,567.84 USD at month 36 with i = 2.00% and j = 1.00% or 3.00% → 108,110.18 and 93,857.59 (tested; NWL rounds the MVA rate to 0.01%). PGF Neo: i = j = 3% with a 0.10% margin → 1.54% deduction at the start of a 20-year period and 0.87% for 15 years, which pins its 0.80 / 0.60 month scaling (tested). DFL Receive: the charge table is selected by age band and rate band (tested).
- **PGF Neo net yield.** The brochure's "no initial charge" reproduces the published 実質利回り for the basic course (4.92% / 5.25%). The withdrawal course publishes a lower figure (3.75% / 3.65%) for the same 積立利率; it is a different measure, most likely including the paid-out 積立金(特則) left to accumulate at PGF's lower rate, so it is not used.
- **Not validated:** NWL's and MSP's survival-weighted maturity values (the insurers' figures include a mortality credit the model leaves out), and surrender values for plans whose MVA reference rate is proxied (MetLife, Nippon Life, T&D FX) or reconstructed (T&D yen: T&D's own averaging is 会社の定める方法, so the JGB figure cannot be checked against a published one).

## Caveats

- **Interest-paying plans rank on yield, not on 返戻率.** DFL Receive, T&D PW6, MetLife periodic and the 定期支払 courses pay their return yearly. Their buyer yield is comparable; their maturity 返戻率 is not, because the payouts are not reinvested. DFL Receive 3's 定期積立 earns an unpublished deposit rate, ignored here.
- **Survival-weighted plans are understated at maturity and not like-for-like.** NWL tontine, MSP annuity-fund and maturity-focused courses, T&D annuity-focused and DFL PCU3 maturity-focused pay a reduced death benefit. The insurers' maturity values include a mortality credit (NWL's brochure example: 138.5–144.2 against a 134.4 roll-up; MSP about 1.1–1.5% above). The model shows the plain roll-up.
- **Deferred annuities show the deferral account only.** DFL Premier Partner and NWL's USD annuity are valued at the end of deferral, as the annuity fund; that fund is paid as an annuity, not a lump sum. Partner's 5–6 and 7–10 year deferral bands are modelled at 5 and 10 years. Its MVA counts months to the last annuity payment; the model counts to the end of deferral only, so Partner's MVA effect is understated.
- **Fullheart and T&D PW6 are whole life.** Fullheart uses SML's published 15-year 実質利回り for age 60, which already reflects mortality and charges; its interim surrender path is not a roll-up and is flagged. PW6 is modelled as interest paid out over the rate period (30/25/20/15 years by age for yen; 10 years USD, 20 years AUD); its published net yield is a different total-return measure and is not used.
- **Margins set at the insurer's discretion.** None of these is published per date, so the maximum is used: T&D 0.05% yen and 0.10% FX (brochure 所定の率; the 約款 allows up to 0.10% for yen too, flagged), MetLife and PGF 0.10%.
- **Dividends and targets.** SML TG3 pays 5年ごと利差配当 on top. Target plans convert early when the target is hit and are shown held to maturity. MetLife's boost rate applies to the first rate period only.
- **Timing.** Annual crediting; surrender just before the policy anniversary; MVA over whole years (insurers count months). No tax.
- **One FX date.** Yen figures use TTM for 17 November 2026. USD and AUD come from T&D's rate page (MS Primary's own page shows the same 159.48 and 107.41). EUR (175.40) and NZD come from MS Primary's 過去5営業日の為替レート page, saved as `sources/msp/rates/past_fx_rates_2026-11-17.html`. That page prints a TTM for EUR but not for NZD; the NZD TTM of 87.73 is the midpoint of its printed TTS 88.23 and TTB 87.23, the same ±0.50 construction as the other three currencies. MUFG Bank's final quotation for 17 November (as republished by MURC, `sources/markets/fx/murc_mufg_ttm_20261117.html`) gives NZD TTS 89.73 and TTB 85.73, the same 87.73 midpoint, and MUFG's own data show its TTM is that midpoint. Only MAREⅢ and Broadway use EUR and NZD.

## Missing attributes by product

What the model needs and the data does not yet hold. Nothing is assumed in its place; the affected outputs are blank or marked indicative.

- **DFL Premier Partner**: MVA duration through the payout period.
- **T&D FTW4, Yasashii Yen Nenkin 2, PW6**: MVA reference rate for the FX plans (a proprietary USD bond index; 積立利率 used as the proxy; the yen plans use the JGB reconstruction); MVA margin (brochure maximum used).
- **MetLife, Nippon Life**: MVA reference rate at issue (MetLife publishes the surrender-date index and margin a on its product page, which refuses automated access; Nippon Life's index is a proprietary Bloomberg BVAL average it does not publish).
- **SML Fullheart**: interim cash values.
- **PGF Hosho Sentaku Neo**: the current MVA margin A (0.10% used).
