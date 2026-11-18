# App evaluation

The rubric agreed with the owner on 2026-11-17, and the score for each round.

## How it is scored

Each test scores 0 (fail: missing or wrong), 1 (weak: present but does not do the job), 2 (acceptable: does the job with rough edges) or 3 (good). An area's score is the average of its tests as a percentage of 3. The overall score is the weighted sum of the areas. **Pass: 75% overall and no test scoring 0 in a must-have area.** Windows is not evaluated. The chat pane and brochure translation are outside the rubric.

| Area | Weight | Must-have |
|---|---|---|
| A. Leaderboard rank check | 40% | yes |
| B. Trust | 20% | yes |
| C. Product pages and documents | 15% | yes |
| D. Update data | 10% | yes |
| E. Editorial consistency | 10% | no |
| F. Other screens | 5% | no |

The main job: every half-month, after rates change, see where each product-plan ranks for a product type, currency and tenor, and what moved, for the same 5 to 10 combinations each time. Judged on seconds to the answer and trust in the numbers. No product is special (neutral ranking).

## Tests

### A. Leaderboard rank check (40%, must-have)

- **A1 Saved boards on one page.** The owner saves their combinations (type, currency, tenor) once; the home screen then shows all saved boards stacked, so a period's check is one scroll with no picking. 0: no saved boards. 1: can save, but boards open one at a time. 2: stacked on one page with rough edges (cannot reorder or remove, or lost on restart). 3: stacked, add/remove/reorder, persists.
- **A2 Readable, top five.** Each stacked board shows its top five rows in comfortable type with "show all" to expand; about three boards per laptop screen. 0: not stacked or no top-five. 1: top five but cramped or tiny, or no way to expand. 2: works, minor crowding. 3: clear at a glance.
- **A3 One row per plan.** Every qualifying plan is its own row, with the plan named in plain English. 0: one row per product only. 2: per plan but plan names unclear. 3: per plan, clear names.
- **A4 Stale rows hidden.** Boards rank only rates in force today; rows whose latest rate is not in force (for example an archive capture from 2024) do not appear on boards at all. 0: stale rows ranked. 1: shown but separated or flagged. 3: hidden from boards (still reachable in charts and product pages).
- **A5 "In force today" by period.** A row is current if its rate period covers today, whatever the period's start date: a two-week window that began on 9 Nov counts on 17 Nov; a half-month period that began on the 1st does not count on the 17th. 0: uses "same date as newest" or no rule. 1: uses an age cut-off in days. 3: uses each insurer's period.
- **A6 Row content.** Each row shows product, plan, rate, change since the previous period, rank change since the previous period, the estimated value after the tenor, and the as-of date, and nothing else (a trend line or other extras count as clutter). 0: two or more required items missing. 1: one required item missing. 2: all present plus clutter. 3: exactly these.
- **A7 Rate measure switch.** Boards rank by crediting rate by default, with a one-click switch to net yield that lists only the plans that publish it. 0: no switch. 2: switch exists with rough edges. 3: works cleanly.
- **A8 Time to the answer.** From launching the app, the owner sees their saved boards without any click, and each board states its leader, the spread and what moved without arithmetic. 0: must pick type, currency and tenor each time. 1: remembers the last single board only. 2: saved boards open on launch, but leader, spread or movers need working out. 3: all immediate.
- **A9 What moved.** On each board it is obvious which rows changed rate or rank since the previous period and by how much. 0: no change information. 1: change shown but against a stale earlier date without saying so. 2: shown and honest. 3: shown, honest, and movers stand out.

### B. Trust (20%, must-have)

- **B1 As-of date on every row.** 0: absent. 2: present. 3: present, and a date that differs from the board's main date is easy to spot.
- **B2 Source in one click.** From a rate on a board, one click opens the saved page the number came from (the snapshot recorded in the data), not just the insurer's home page. 0: no route to the source. 1: reachable in several steps, or opens only the live URL. 3: one click to the saved snapshot.
- **B3 Unchecked data is marked.** Data that has not passed the independent re-check is visibly marked wherever its rates appear, and data that has passed is not. The data records which is which (`verified_through` per product: rows retrieved after that date are unchecked). 0: no mark exists. 2: marked, but not on every screen where rates appear. 3: marked consistently, with a one-line explanation available.

### C. Product pages and documents (15%, must-have)

- **C1 Full rate grid.** All plans, currencies and tenors (and age bands or sexes where they exist) for the current period are visible on one screen without choosing from dropdowns. 0: one small table behind pickers. 1: grid for one plan at a time. 2: full grid, hard to read for the largest products. 3: full and readable.
- **C2 Documents.** One click from the product page opens the right brochure or policy PDF; documents are labelled in English with their date. 0: cannot open. 1: opens but labels are Japanese-only or undated. 3: one click, clear English labels and dates.
- **C3 Product facts.** Insurer, policy name, product type, currencies, sale status, launch date (or a clear "not established"), distributors, and notes on gaps are on the page. 0: mostly absent. 2: present, hard to scan. 3: present and scannable.
- **C4 History.** A chart of the product's rates over time for a chosen plan, currency and tenor, with gaps shown as gaps and archive-derived points marked. 0: no chart. 1: chart joins points across long gaps as if continuous. 3: honest chart.

### D. Update data (10%, must-have)

- **D1 Scrape from the app.** One button refreshes the directly reachable insurers, with per-insurer status and a count of new data points, and reloads the data afterwards. 0: absent or broken. 2: works with rough edges. 3: works cleanly.
- **D2 Restricted insurers.** Mitsui Sumitomo Primary and Nippon Wealth are reported plainly as not reachable from this network, with what to do about it, and are not presented as failures of the app. 0: silent or shown as a crash. 2: reported but unclear. 3: clear.

### E. Editorial consistency (10%)

- **E1 One visual system.** Every screen holds the Editorial style: same type scale, spacing and colour, no default-looking buttons, selects or scrollbars-as-layout. 0: several screens off-style. 2: minor lapses. 3: consistent.
- **E2 Terse English.** Labels are short and in English; Japanese appears only as data (document titles, distributor names, the Japanese product name as a secondary line). 0: Japanese labels or wordy explanations throughout. 2: minor lapses. 3: consistent.
- **E3 No internal keys.** No raw identifiers such as "USD kakutei p5", "a2_jpy_h10-12" or "crediting_rate" are shown to the reader. 0: common. 1: on several screens. 2: rare. 3: none found.

### F. Other screens (5%)

- **F1 Moves**, **F2 Markets**, **F3 Chart**: each works and does not get in the way. 0: broken. 2: works with rough edges. 3: works.

## Score history

| Round | Overall | A | B | C | D | E | F | Must-have zeros | Result |
|---|---|---|---|---|---|---|---|---|---|
| 1 (baseline) | 47.9% | 25.9% | 33.3% | 83.3% | 83.3% | 55.6% | 88.9% | A1, A2, A4, A7, B3 | Fail |
| 2 | 90.8% | 92.6% | 88.9% | 91.7% | 100% | 77.8% | 88.9% | none | **Pass** |
| 3 | 95.1% | 96.3% | 100% | 91.7% | 100% | 77.8% | 100% | none | **Pass** |
| 4 | 94.3% | 100% | 88.9% | 91.7% | 100% | 77.8% | 100% | none | **Pass** |
| 5 | 96.0% | 100% | 100% | 91.7% | 100% | 77.8% | 88.9% | none | **Pass** |

Round 1 was the app as first built. Round 2 followed one round of fixes and passed. Rounds 3, 4 and 5 each followed fixes to the previous round's open items and confirmed the pass. Round 5 was the last round allowed. Each round was scored by a separate evaluator on a frozen copy of the app, without sight of earlier rounds, so differences of a point or two between rounds partly reflect the evaluator.

Round 5 recomputed every board for both rate measures and found them correct, and on an altered copy the app's ranks, shared ranks for ties, rank changes, "new" and "n/a" labels all matched an independent calculation. The unchecked mark appeared everywhere a rate is printed, the source window included.

### Open items from round 5 (not fixed)

By points: on the largest product the age-band labels are 10.5px and break mid-range, so the grid is hard to read (C1); chart date labels, one product name in search results and a wrapping board summary are inconsistent (E1); the Data page repeats the "needs a Japan connection" message three times and its logs lack context (E2); Markets' EUR and NZD small charts are a few scattered archive dots (F2).

Bugs outside the rubric: the source window names the product and plan but not the currency and tenor of the rate clicked; Premier Receive 3 shows "Standard" as its plan name on Moves; tied ranks in the Explore history legend lack the "=" sign.

Not covered by any round: the chat pane and brochure translation (outside the rubric), dark mode, Windows, refreshing Dai-ichi Frontier and Sumitomo Life from the app, and updates that include documents.

## Branch evaluation: add-recommended-products (2026-11-17)

Scored once by an independent evaluator after merging main into the branch. The branch adds 31 candidate products from 12 insurers, kept out of every screen until enabled on the Candidates page, so it was scored twice.

| Pass | Overall | A | B | C | D | E | F | Must-have zeros | Result |
|---|---|---|---|---|---|---|---|---|---|
| 1. As shipped, no candidate enabled | 94.9% | 100% | 100% | 91.7% | 83.3% | 77.8% | 100% | none | **Pass** |
| 2. All 31 candidates enabled | 78.6% | 92.6% | 77.8% | 58.3% | 83.3% | 55.6% | 66.7% | none | **Pass** |

Boards were recomputed independently in both passes (52 and 103 boards) and matched row for row, including monthly-period products. Candidates did not leak into any screen while disabled.

Why pass 2 is lower: saved pages that are not HTML (MetLife, Manulife) cannot be opened from a rate (B2); many candidate documents are undated and MetLife's bank editions share one English title (C2); charts assume half-month periods and break monthly series into stubs (C4); some plan names from the candidate files are unclear, contain Japanese or expose internal codes (A3, E2, E3); long names crowd the boards (A2).

Data concerns found: Credit Agricole's tenor is the payout period, not the deferral, so its place on 20Y and 30Y boards and its "after 20Y" value mislead; Premier Partner's age bands are internal codes that no sample age matches; "Index-Linked Annuity 2" is typed as a fixed annuity.


### Improvement passes (target: 90% with all candidates enabled)

| Stage | As shipped | All candidates enabled | A | B | C | D | E | F | Must-have zeros |
|---|---|---|---|---|---|---|---|---|---|
| Before (first branch evaluation) | 94.9% | 78.6% | 92.6% | 77.8% | 58.3% | 83.3% | 55.6% | 66.7% | none |
| Pass 1 | 96.1% | **91.3%** | 100% | 100% | 75.0% | 83.3% | 66.7% | 100% | none |

Area columns are for the all-candidates state. The 90% target was met on pass 1 of the five allowed, so no further pass was run. Pass 1 corrected tenors and age bands in the candidate data, gave plans and documents clear English names, made non-HTML saved pages open from a rate, and made charts follow each insurer's rate period. The evaluator recomputed 138 boards (343 rows) with candidates enabled and 75 boards as shipped; all matched, as did ranks, ties and rank changes on altered data.

Open items after pass 1, by points: ten Nippon Wealth form-based products, Mirai ni Tsunageru v2, Long Dream GOLD 3 and Sodateru Kajitsu have no dated, downloaded documents (C2, scored 1); the Update data log has garbled and wordy lines and a wrapping insurer name (D1, E2); Mirai ni Tsunageru v2 stacks 13 age bands in each grid cell (C1); the Yuyu Jikan USD and AUD tables overflow their column and one board header wraps (E1); "NWL form: ..." product names expose an internal code (E3).

Outside the rubric: the source-window highlight lands on the first matching figure, not always the clicked row (Manulife text snapshots, T&D male/female columns). Data to review: Credit Agricole's "after 2Y" value is shown for a rate that applies over 22 to 32 years; Tanoshimi Global III's index plan and Premier Partner's with-index plan carry the same rates as their fixed plans; Long Dream's net yield equals its crediting rate; Yuyu Jikan USD life-annuity age bands read "6-69" and "11-69"; whole-life boards mix rate-lock and reset tenors.

### After pass 1: open items fixed (not re-scored)

App: Update data log rewritten as short plain lines with insurer short names and a separate source column; products with more than four age bands get one row per band; Yuyu Jikan tables fit their column; board headers stay on one line; the source window marks the clicked row's cell, or says "n matches" when the saved page cannot tell them apart; single-date charts get a date axis and legends list only drawn series.

Data: the ten Nippon Wealth form-based products have plain English names; Credit Agricole's rate has no comparable tenor, so its tenor is blank and it is off the boards (168 rows, values unchanged); identical index and fixed rates on Tanoshimi Global III and Premier Partner, Long Dream's net yield and the Yuyu Jikan age bands were re-parsed from the saved pages and are correct, with a reader note on each.

Still open: documents. Long Dream GOLD 3 now has dated policy conditions and Mirai ni Tsunageru v2 an archived November 2024 brochure, but the Nippon Wealth (403), Manulife (403) and PGF (bot-protection page) documents could not be fetched from this network and the Wayback Machine rate-limited requests. The Nippon Wealth policy-guide pages to fetch from a connection in Japan are https://www.nw-life.co.jp/shiori/gNN/ for NN = 14, 27, 30, 39, 41, 42, 43, 44, 45, 46.
