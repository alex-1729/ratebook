## Crediting rates available

| # | Product | Published metrics | Current rates read? | History available | Rate page format |
|---|---------|-------------------|---------------------|-------------------|------------------|
| 1 | SML TG3 | 積立利率, 実質的な利回り, FX, MVA rate | Yes (collector submits the form) | **Yes**: 積立利率 and MVA rate semi-monthly from 2023-06-16 (probably the launch period) to 2026-11-16, from the site's own history pages. Wayback captures of the page hold only the form's auto-submit stub, no rates (checked 2026-11-17) | Form-driven page (JSF POST) |
| 2 | DFL PCU3 | 積立利率, 実質利回り (USD/AUD/JPY); 満期重視プラン 積立利率; MVA rate | Yes | **Yes**: 積立利率, 実質利回り and MVA rate semi-monthly from 2023-04-01 to 2026-11-16 (88 dates each, from the site's history tables) | Static HTML |
| 3 | DFL PR3 | 積立利率, 定期積立率 (M/F) by age band and guarantee period | Yes | From 2026-11-01 only (launched 2 Nov) | Static HTML |
| 4 | DFL PR2 | 積立利率, 定期支払率 (M/F); 基本/指数プラン | Yes | 積立利率 and 定期支払率 (M/F), 基本 and 指数プラン: semi-monthly from 2024-01-01 to 2026-11-16, 70 rows each (checked 2026-11-16; the earlier note that 定期支払率 (F) stops at 2026-10-01 was wrong) | Static HTML |
| 5 | MSP MR3 | 積立利率 by course / currency / term; FX | Yes, live from a Japan connection (`msp.py`, or `collect_all.py` when the probe answers); from other networks the rate server times out, and a saved copy is ingested with `msp.py --file` | **Thin**: 4 contract-date windows (2025-01-20, 2025-10-13, 2026-06-08 from Wayback; 2026-11-09 from a saved live copy). Wayback holds only these 3 captures of the rate page, which has used product_id=67 since at least 2020 (checked 2026-11-17) | Server page (`RateList.do?product_id=67`). FX rates change every business day; 積立利率 is set per two-week contract-date window starting on a Monday (confirmed on a saved live copy, 2026-11-09 to 11-22). |
| 6 | NWL YY | 積立利率; 基準金利 | Yes, live from a Japan connection (`nwl.py --live`, or `collect_all.py` when the probe answers); from other networks the site returns 403, and saved copies are ingested with `nwl.py --live-dir` | **Thin**: 積立利率 for 17 half-month periods 2020-07-01 to 2026-11-16 (the 2026-11-16 period read live on 2026-11-17), 基準金利 for 9 dates from 2019-12-16, with large gaps. All 26 Wayback captures of the rate page are used; the one 2019 capture is a bot-block stub, so there is nothing from 2019 (checked 2026-11-17) | Static HTML (`gu_int_rate.html`, `gu_int_rate*_base.html`) |
| 7 | TDF FTW | 積立利率, 実質的な利回り | Yes | **Thin**: 7 half-month periods (2024-12-01, 2025-08-01, 2025-10-01, 2026-01-16, 2026-05-16, 2026-06-01 from Wayback, incl. the distributor copy `r_tumitate_riritu.html`; 2026-11-16 live). The live page is overwritten each period, so history grows only by running the collector | Static HTML |
| 8 | DFL PCU3 Rakuten (楽天銀行エディション) | 積立利率, 実質利回り (USD/AUD/JPY, 5Y and 10Y only); 満期重視プラン 積立利率; MVA rate | Yes | **Yes**: 13 dates from 2026-06-01 to 2026-11-16 from the site's history tables. The 2026-06-01 row predates the 2026-06-15 launch and 2026-06-15 is a one-off row; both are kept as published | Static HTML (same policy form as PCU3, own rate pages) |

**返戻率 (5年後 / 10年後)** and PR **10年実質利回り** are not published by any insurer. Calculate them from 積立利率: for TDF, (1+r)^n reproduces all four of the sheet's Sep 1H figures.

## Current rates read (16–30 Nov 2026)

### DFL PCU3: 基本プラン

| Currency | 積立利率 5Y | 積立利率 10Y | 実質利回り 5Y | 実質利回り 10Y |
|----------|------------|-------------|--------------|---------------|
| USD | 5.08% | 4.95% | 4.33% | 4.51% |
| AUD | 4.73% | 5.55% | 3.98% | 5.10% |
| JPY | 2.62% | 2.65% | 2.310% | 2.421% |

JPY 15Y / 20Y 積立利率 are 2.69% / 2.71%.

### DFL PR3

定期積立率 is shown by sex (M / F). Longer guarantee periods (30Y for USD and JPY, 20Y for AUD) are offered only to the younger age bands.

| Currency | Age | Guarantee | 積立利率 | 定期積立率 M | 定期積立率 F |
|----------|-----|-----------|---------|-------------|-------------|
| USD | 0–65 | 10Y | 5.01% | 5.08% | 5.06% |
| USD | 0–65 | 30Y | 5.08% | 5.16% | 5.14% |
| USD | 66–75 | 10Y | 4.98% | 5.08% | 5.04% |
| USD | 66–75 | 30Y | 5.03% | 5.16% | 5.12% |
| USD | 76–80 | 10Y | 4.93% | 5.08% | 5.02% |
| USD | 81–85 | 10Y | 4.85% | 5.07% | 4.99% |
| USD | 86–90 | 10Y | 4.73% | 5.03% | 4.93% |
| JPY | 0–65 | 10Y | 2.97% | 3.00% | 2.99% |
| JPY | 0–65 | 30Y | 3.58% | 3.62% | 3.61% |
| JPY | 66–75 | 10Y | 2.95% | 3.00% | 2.98% |
| JPY | 66–75 | 30Y | 3.54% | 3.62% | 3.59% |
| JPY | 76–80 | 10Y | 2.90% | 2.99% | 2.95% |
| JPY | 81–85 | 10Y | 2.81% | 2.93% | 2.88% |
| JPY | 86–90 | 10Y | 2.66% | 2.82% | 2.77% |
| AUD | 0–65 | 10Y | 4.79% | 4.85% | 4.84% |
| AUD | 0–65 | 20Y | 5.23% | 5.30% | 5.29% |
| AUD | 66–75 | 10Y | 4.75% | 4.85% | 4.81% |
| AUD | 66–75 | 20Y | 5.17% | 5.29% | 5.25% |
| AUD | 76–80 | 10Y | 4.70% | 4.85% | 4.79% |
| AUD | 76–80 | 20Y | 5.11% | 5.29% | 5.23% |
| AUD | 81–85 | 10Y | 4.58% | 4.79% | 4.71% |
| AUD | 86–90 | 10Y | 4.40% | 4.68% | 4.59% |

Read from the history tables' 2026-11-16 row.

### DFL PR2: 10Y guarantee

定期支払率 is shown by plan (基本 / 指数) and sex (M / F).

| Currency | Age | 積立利率 | 基本 M | 基本 F | 指数 M | 指数 F |
|----------|-----|---------|-------|-------|-------|-------|
| USD | 0–65 | 5.01% | 5.08% | 5.06% | 3.59% | 3.57% |
| USD | 66–75 | 4.98% | 5.08% | 5.04% | 3.59% | 3.55% |
| USD | 76–80 | 4.93% | 5.08% | 5.02% | 3.59% | 3.53% |
| USD | 81–85 | 4.85% | 5.07% | 4.99% | 3.58% | 3.50% |
| USD | 86–90 | 4.73% | 5.03% | 4.93% | 3.54% | 3.44% |
| JPY | 0–65 | 2.97% | 3.00% | 2.99% | 2.78% | 2.77% |
| JPY | 66–75 | 2.95% | 3.00% | 2.98% | 2.78% | 2.76% |
| JPY | 76–80 | 2.90% | 2.99% | 2.95% | 2.77% | 2.73% |
| JPY | 81–85 | 2.81% | 2.93% | 2.88% | 2.71% | 2.66% |
| JPY | 86–90 | 2.66% | 2.82% | 2.77% | 2.60% | 2.55% |
| AUD | 0–65 | 4.79% | 4.85% | 4.84% | 3.36% | 3.35% |
| AUD | 66–75 | 4.75% | 4.85% | 4.81% | 3.36% | 3.32% |
| AUD | 76–80 | 4.70% | 4.85% | 4.79% | 3.36% | 3.30% |
| AUD | 81–85 | 4.58% | 4.79% | 4.71% | 3.30% | 3.22% |
| AUD | 86–90 | 4.40% | 4.68% | 4.59% | 3.19% | 3.10% |

積立利率 is the same for 基本プラン and 指数プラン. Read from the history tables' 2026-11-16 row.

### TDF FTW4

| Plan | 積立利率 2Y | 積立利率 5Y | 積立利率 10Y |
|------|------------|------------|-------------|
| JPY 年金重視コース | n/a | 2.51% | 3.28% |
| JPY 介護認知症保障コース | n/a | 2.16% | 2.87% |
| USD, 保険金最低保証特約なし | 3.19% | 3.89% | 4.56% |
| USD, 保険金最低保証特約付加 | 2.97% | 3.65% | 4.29% |
| AUD, 特約なし | 3.60% | 3.72% | 4.25% |
| AUD, 特約付加 | 3.33% | 3.39% | 3.72% |

The 2Y term is offered on the 外貨プラン (USD, AUD) only; the 円貨プラン has 5Y and 10Y.

実質的な利回り equals 積立利率 (including 2Y) except AUD 5Y: 3.71% (特約なし) and 3.38% (特約付加).

### Not shown above

SML TG3, MSP MR3, NWL YY and DFL PCU3 Rakuten rates are in `data/rates/` but not summarised here. Nor are the 31 candidate products (`"candidate": true` in `data/products/`): their rates are collected too and can be viewed in the app once enabled on its Candidates page.
