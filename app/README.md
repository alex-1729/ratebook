# Ratebook

A personal, offline Electron app (macOS and Windows) that displays the products, rates and documents in this repository's `data/` and `sources/` folders.

Personal use only. Do not distribute it together with the insurers' PDFs. The app never copies them: it opens them in place from `sources/`.

## Run

Requires Node.js 20 or later.

```bash
cd app
npm install
npm start
```

`npm install` downloads Electron and the build tooling. After that the app itself makes no network requests: every non-local request is blocked in the main process and by the page's content security policy, and there is no telemetry, analytics or auto-update. Links to source pages and original PDFs are handed to your default browser only when you click them.

Three parts of the app cause network traffic, and only when you use them: the update run on the **Data** page (the repository's Python collectors fetch the insurers' public pages, and for news the regulator's and association's, when you press Run; the optional news summaries step sends the titles and the public release text to Claude through your sign-in), the **assistant** pane (Claude Code talks to Anthropic with your own Claude Code sign-in when you send it a message), and **Translate** in the document viewer (the repository's translation engine sends the page text to Claude through the same sign-in when you press Start). All three are described below. Nothing else does.

## Data folder

- Started with `npm start`, the app reads the folder above `app/` (this repository).
- A data folder may keep `data/`, `sources/`, `reports/` or `translations/` as symlinks to another disk; files are still refused when they resolve outside the folder and outside those links' targets.
- **Choose folder** (Data page, or File > Choose Data Folder, Cmd/Ctrl+O) picks another one. Choose the folder that contains `data/` and `sources/`. The choice is remembered in `settings.json` in the app's user-data folder (`~/Library/Application Support/Ratebook` on macOS, `%APPDATA%\Ratebook` on Windows). The app was called Insurance Rates Viewer before; on its first start as Ratebook it copies `settings.json` and the page's saved view state (filters, enabled candidates) from that name's folder. The old folder is left in place.
- A packaged build has no default folder, so it asks you to choose one on first start.
- `IRV_DATA_DIR=/path/to/folder npm start` sets the folder for one run when nothing has been remembered.
- **Reload** (Cmd/Ctrl+R) re-reads everything after the Python collectors have run. Rates are refreshed only by running the collectors, from a terminal or from the Data page.

## What it shows

The interface is English; Japanese appears only where it is the data (the Japanese product name under the page title, original document titles, distributor names). Search matches Japanese names too.

- **In force today**: a board ranks only plans whose rate is in force today, by each insurer's own rate period (`rate_period` in `data/insurers/`): half-months (1st-15th, 16th-end) or, for Mitsui Sumitomo Primary, 14-day windows starting on the row's date. Plans with no rate in force are never ranked; one line per board says how many were left out and lists them with their last date. Change and rank change compare with the period immediately before only; where a plan has no point for that period the change is a dash (the tooltip gives the last earlier date) and the rank change is "new".
- **Source in one click**: clicking a rate (boards, Explore, Moves, the product grid, the history table) opens the saved page that number was read from, in a Source window that jumps to the figure and shows "Saved copy · retrieved <date>", a link to the original URL, and the unchecked note when that rate has not been re-checked. No file path is shown. Only files inside `sources/` of the data folder can be opened, scripts are off, and nothing is fetched.
- **Unchecked mark**: a small ○ follows any rate retrieved after its product's last independent re-check (`verification.verified_through`), or belonging to a product with none, on every screen where rates appear, including chart read-outs. Hovering it gives the date; a one-line key appears in the footnote when any mark is on screen.
- **Explore** (home): the leaderboard, shown as soon as the app opens. One board chosen with three pickers: product type, currency and tenor (each narrows the next and counts the plans with a rate in force). It shows a header with the leader, the spread and what moved, every ranked row, the plans left out (only those that once published a rate for that currency and tenor, with their last rate), a history chart of the top eight, and the measure switch (crediting rate or net yield). Plans on the same rate share a rank and show it with an "=" prefix (1, =2, =2, 4), on the board and in the history legend. A product with only one plan shows no plan label (no "Standard") on boards, Moves and legends, as on its product page.
- **As of** (Explore, Markets, Moves): a date picker next to the measure switch shows the rates in force on an earlier day. The title gains "as of <date>", the board's date, changes and rank changes are those of that day, and **Today** goes back. The choice is kept for the session only.
- **Net-yield marks**: on a net-yield board, a ✓ after a published net yield means the value model (`reports/like_for_like_value.json`) reproduces it from the crediting rate and the brochure's up-front charge; ≠ means it does not (hover for the figures and where the charge comes from). No mark means there is no brochure charge to check against; a small "=" means the insurer states the net yield equals the crediting rate, so it is not checked.
- **Modelled net yields**: a plan with no published net yield, and no statement that it equals the crediting rate, is ranked on a net-yield board at the value model's yield where the model has one: the crediting rate less the up-front charge, (1 + rate) × (1 − charge)^(1/term) − 1, or the crediting rate itself where there is no charge. It carries a small "m" (hover for the figures and where the charge comes from) and the leaderboard tool reports it as `basis`. The model runs on the latest period only, so a modelled yield has no Chg, no history and no value under **As of** an earlier day, and it disappears once the crediting rate has moved until `scripts/value/run.py` is run again. Left out: payout plans (`_p<years>`), whose term is not their horizon, plans the model does not cover, and payout-rate plans. Modelled yields are not shown on the product page or by `get_rates`.
- **Over government yields** (Explore): where the currency has a curve in `data/markets/govt_yield__<CCY>.csv` (UST, JGB, ACGB), the board has an Over column: the rate less the government par yield, converted to annual effective, at the nearest curve tenor to the plan's tenor, using one yield per curve, the latest on or before the board's date (within 14 days). The header shows that yield. Plans whose tenor comes from the plan (payout plans, judgement periods) are marked with the tenor used; life annuities and immediate payouts have none. Same rules as `scripts/rate_spreads.py`.
- **Products**: every product grouped by insurer, with its type, the number of plans, and the currencies and tenors that have a rate in force today; a type filter narrows the list. Clicking a row opens the product page, whose rate grid shows every plan, currency and tenor.
- **Product types** come from `data/product_types.json` and each product's `product_type`; a `rate_series` entry's own `product_type` overrides it for that plan, and its `plan_en` is the plan label.
- **Markets**: for one product type at a time: a headline and three figures about the selected currency and tenor, six small charts (USD / AUD / JPY at 5Y and 10Y, one line per product, with a product legend) that select the case, currency and tenor toggles limited to what exists for the type, a table of every plan with a rate in force, and the type's latest moves across all currencies.
- **Moves**: every series that changed between the period before and the current one, biggest first, with currency / insurer / measure filters, plus cells newly offered or suspended. Series with no point for the previous period are counted, not compared.
- **Chart**: up to eight series of any product, measure, plan, currency and tenor on one step chart. Each series is named once, in the list under the chart, which also shows its value at the pointer date; 6M / 1Y / All presets and drag to zoom. Breaks mean not offered; archive captures are hollow points on dashed lines. Points more than 20 days apart are never joined: an isolated capture is a marker with a half-month stub, so a gap does not read as a steady rate.
- **Product page** (via Find product, which can be filtered by product type, or a product name on any board): a compact facts block at the top (insurer, product type, currencies, status, launch date or "not established", distributors, re-check date, policy form, then the reader notes from `notes_en`), and three tabs. **Rates** shows every plan, currency, tenor and age band for the current period with nothing to choose: captioned grids by currency and deferral period (one per currency when plans are tied to one currency each), each plan a heading row with its age bands under it, and a separate grid with no tenor axis for plans whose term is part of the plan. Rows with no rate in force are folded behind one link. A tenor of zero is headed "Immediate". **History** opens on the series in force for the sample case and has the chart and a paged table of points. **Documents** lists them by English title with the embedded viewer.
- **News**: what the tracked insurers, the FSA, the Life Insurance Association and the press have published. Newest first, by month, with filters for insurer, source, topic and period, and a search of titles. Routine notices (maintenance, awards, campaigns) are hidden until you tick "Include routine items". Each row has the English and Japanese title, a short summary for key items, who published it, and marks: New (collected since you last opened News), Key, Snippet only (known from a search result, not from the page) and Checked (re-checked against the source). A title opens the source in your browser. **Stories** at the top are named threads such as a regulatory case, each with an overview, key dates, open questions and a timeline. **Sources** at the bottom lists every news list with when it was last read; a list not read for more than 8 days, blocked or failed is flagged, and the count shows under the page title.
- A product page has a **News** tab: items that name the product, then key news about its insurer from the last 12 months.
- **Data**: data folder, reload, and the update run described below.

## Assistant

**Ask** in the top bar (or Cmd/Ctrl+J, View > Assistant) opens a pane on the right, on every screen, where you can ask about the rate data, product facts and the documents of the product that is open. It remembers whether it was open and how wide you dragged it.

What it is: the app has no chat of its own and uses no API key. The pane runs the `claude` program that ships with `@anthropic-ai/claude-agent-sdk`, in a pseudo-terminal (`node-pty`, drawn with xterm.js), with your existing Claude Code sign-in. This is the same approach as the Tessera app, and parts of `src/chat/` are adapted from it. If you have never signed in, run `claude` once in a terminal and sign in.

What it can do:
- **App tools**, answered from the same in-memory data the screens show: `news`, `list_stories`, `list_insurers` (news items, stories and coverage per insurer), `open_news`, `list_products`, `get_product`, `get_rates`, `leaderboard` (exactly the Leaderboard screen, with as-of dates and stale flags), `moves`, `current_view`, `list_documents`, `read_document` and `search_document` (page text and text search of a product PDF, extracted by the app's own PDF engine), and three that only navigate: `open_product`, `open_leaderboard`, `open_document` (switches to the Documents tab and jumps the viewer to a page). Every argument is checked in the main process first.
- **Built-in tools**: only Read, Glob, Grep and the question form. Every read is checked by the app and allowed only inside `data/`, `reports/`, `translations/` and `sources/<insurer>/docs/` of the data folder. There is no shell, no file writing and no web access. It cannot run the collectors or change data.
- **What it knows about the window**: each message you send is prefixed with one line saying which screen, product, type / currency / tenor, document and page are showing, so "this product" and "this page" work.
- **Citations**: answers from a document cite it as `[doc:<product_id>/<name> p.<page>]`. Each one appears as a "Cited" button above the terminal that jumps the embedded viewer to that page. Page numbers are PDF page numbers, which can differ from the number printed on the page.
- It is instructed to be terse, to give every figure with its as-of date and source, to say when a figure is stale, Wayback-derived or marked unverified in the product's notes, and not to give personal financial advice or recommendations.

Conversations: **New conversation** starts a fresh one; **Conversations** lists the ones started in this app for this data folder, newest first, with Open and Delete (asks once more, then removes that transcript for good). The data folder is also where your own Claude Code sessions for the repository are kept, so the app records the ids of the conversations it starts (in its `settings.json`) and never opens, lists or deletes any other.

Limits and things to know:
- Claude Code is started with only the app's instructions, tools and hooks. Your own Claude Code settings, plugins, MCP servers and project memory are not loaded, and its telemetry, error reports and self-update are switched off for the pane (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`).
- The app adds nothing about you or the machine to what is sent: only what you type, the one-line description of the window, and what the tools and file reads return. Claude Code itself includes its usual context, such as the working folder's path and the operating system.
- Claude Code treats a typed line starting with `!` as a shell command and `#` as a note to save. Neither is wanted in a read-only pane, so `!` and `#` typed or pasted into it are turned into their full-width forms (！ ＃) before they reach it. Its other slash commands still work.
- Document answers come from the PDF's text layer. Figures, tables drawn as images and scanned pages have no text, and a capture the manifest marks incomplete cannot be read.
- Usage counts against your Claude plan. The model is `sonnet`; `IRV_CHAT_MODEL` names another.
- **When Claude Code cannot run** (the SDK's `claude` binary is missing, node-pty cannot load, or you are not signed in), the pane shows one plain line saying which and what to do, and the rest of the app is unaffected. `IRV_CLAUDE_BIN=/path` points the pane at another binary; a path that does not exist shows the not-installed line (used by the tests).

## Document viewer

The Documents tab of a product page lists its documents on the left (English label, original Japanese title, retrieval or capture date, size, edition, and "Wayback copy" / "Incomplete" marks from the manifest) and shows the selected one in an embedded viewer. Complete live copies are listed first.

- Toolbar: Contents (side rail on/off), page number and count, zoom out / in, Width (fit width), Page (fit page), and Find in document with match count and previous / next. Japanese text is searchable.
- Side rail: the PDF's own outline when it has one, and page thumbnails.
- Keys: Left / Right or PageUp / PageDown change page, Home / End jump to the ends, `+` / `-` zoom, Cmd/Ctrl+F goes to Find, Enter and Shift+Enter step through matches, Esc clears the search.
- The last page of each document is remembered until the app is closed.
- Pages and thumbnails are drawn only as they come into view. Drawing pauses while the window is hidden and resumes when it is shown.
- "Open externally" opens the file in the system PDF viewer; "Original URL" opens the insurer's link in the browser.

How it works: PDF.js (`pdfjs-dist`) is installed under `node_modules` and loaded from there, worker included; nothing comes from a CDN. The page gets a document's bytes through one preload call that serves only PDFs listed in a `sources/*/docs/manifest.csv` of the chosen data folder, after checking that the resolved path stays inside that folder. PDF.js's character maps, fonts and decoders are read from the app's own `pdfjs-dist` folder by a second call that accepts only a fixed set of folders and plain file names. PDFs are never copied into the app or a build. A document that is on disk but not in a manifest can only be opened externally.

## Candidates

A product whose file in `data/products/` has `"candidate": true` is kept apart: it is loaded and its rates keep being collected, but it does not appear in the leaderboard, markets, moves, chart, search or the assistant. The **Candidates** page (top navigation, with the number waiting) lists these products by insurer with an **Enable** button each, an enable-all per insurer and for the whole list, and, once enabled, **Open** and a **Disable** button to move one back.

Enabling is remembered by the app on this computer (its own storage, not the data folder). To make a product a normal one for everyone who uses the data folder, remove `"candidate": true` from its product file.

## Translating a document

**Translate** in the document viewer's toolbar sends the open document for an English translation that keeps the page layout. The work is done by the repository's translation engine (`scripts/repaint/run.py`, not part of the app); the app starts it, shows progress and displays the result. It uses your Claude Code sign-in, like the assistant, and no API key. The result is a machine-made working translation for your own use.

- **Starting**: choose **This page** or **Whole document**; one line says what will happen and roughly how long (about 2-3 minutes for a page, about 10 minutes for a 24-page brochure the first time, much less when pages are already cached), then **Start**. A whole-document run does the page you are reading first. One translation runs at a time; a second request is refused with a message.
- **Views**: **JP** is the original; **EN** shows each page's English version where there is one, and the original with a mark ("Not translated yet", "Queued", "Translating…") where there is not; **Side by side** puts the original on the left and the English on the right, scrolling together. Pages appear as they finish, without the document reloading or your place moving. Find searches the English pages in EN view (and in Side by side when you type Latin letters), the original otherwise.
- **Progress**: a strip under the toolbar shows pages done, the current stage and **Cancel**; the thumbnail rail marks each page (queued, translating, EN, EN · review, failed). Cancel stops the engine; pages already finished are kept. Quitting during a run asks first.
- **Review marks**: the engine checks each page's layout and wording. A page it flags shows "Review · n errors, m warnings"; clicking it lists what to check on that page. The page is still shown. A page that failed shows the original with the reason.
- **Documents list**: a document with an English version is marked "English · n pages · date" (or "partial"), with **English PDF** to open the assembled file in the system viewer and **Delete translation**, which asks once more and then removes that document's English PDF and pages. The engine's cache and translation memory are kept, so translating it again takes seconds per page and no new Claude calls.
- **Where things go** (inside the data folder; `translations/` is git-ignored): pages in `translations/<insurer>/<product_id>/<document>/pages/pNNN.pdf`, the assembled PDF and its result file beside that folder as `<document>.en.pdf` / `.en.json`, the engine's work in `translations/_work/<product_id>/<document>/`. `<document>` is the PDF's file name without `.pdf`, for example `brochure_general_2026-11-16`.
- **The assistant** can tell whether a document has an English version (`document_translation`) and read or search the English pages (`language: "en"`). It is told they are machine-made and cannot start, cancel or delete a translation.

Setup: the engine needs the repository's own Python environment at `.venv` in the data folder. If it is missing, Translate says so and does nothing; the app does not create it. To set it up, from the data folder:

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r scripts/repaint/requirements.txt -r scripts/repaint/requirements-checks.txt
```

On Windows use `py -3 -m venv .venv` and `.venv\Scripts\pip install -r scripts\repaint\requirements.txt -r scripts\repaint\requirements-checks.txt`. If Claude Code is not installed or not signed in, the engine stops and its message is shown in the strip.

How it is kept safe: the page names a document only by its manifest path and a page by its number; the main process checks both against the manifests, builds every file path and the engine's arguments itself, and runs the engine without a shell from the data folder. Translated pages are read through the same guarded access as the originals. `IRV_TRANSLATOR=files` makes the engine replay finished work with no Claude calls (used by `test/translate-steps.json`).

## Update data

The Update section of the **Data** page runs `scripts/collect_all.py` from the chosen data folder.

- Tick the insurers to refresh (all by default), optionally **Include documents** (also re-downloads the PDFs: slower, tens of MB), and press **Run**. Nothing runs at start-up or on a timer. A full run without documents takes roughly 2-4 minutes because the collectors pause between requests.
- Each insurer shows waiting / running / updated / needs a connection in Japan / failed with the collector's message and the number of new data points. `CONFLICT` lines (a site now shows a different value from the one on file) are listed separately. The Log retells the collector's output with insurer, product, measure and plan names; its raw text is under a collapsed **Technical log**.
- **Cancel** stops the collector and the per-insurer script it is running. Only one run at a time. Closing or quitting the app during a run asks first, then stops it. If the app is killed from outside or crashes, a collector already started may keep running to the end.
- When a run ends the dataset is reloaded, so the product screens show the new rates. The time and outcome of the last run are kept in the app's `settings.json`, not in the repository.
- **Python 3.9 or later is required** on the PATH. The app tries `python3`, then `python`, then `py -3` on Windows, and says so on the page if none works. The collectors themselves use only what the repository's scripts need; the app installs nothing.
- **Japan connection:** Mitsui Sumitomo Primary Life and Nippon Wealth Life only answer from some networks. Turn on a Japan connection (VPN) before pressing Run. Without one they finish as "Not reachable from this network" after about 20 seconds each, which is an expected outcome, not an error. The app does not change network settings or try to work around it.
- **Saved copies only:** PGF Life and Manulife are collected from page copies saved under `sources/`; no request is made to their sites. They finish as "Saved copies re-read", and add nothing until a newer copy is saved.
- **News** (the second choice under Update) runs `scripts/news.py collect` for the ticked sources: the insurers' own news lists, the FSA, the association and parent groups. With **Add English titles and summaries** ticked it then runs `scripts/news_enrich.py`, which uses Claude Code with your sign-in and counts against your Claude plan; untick it for a run that makes no model calls. New items show in Japanese until that step has run. Press coverage is not part of this run.
- If `scripts/collect_all.py` is missing from the chosen data folder (for example a copy of only `data/` and `sources/`), Run is disabled and the page says why.
- The command is fixed: the page can only choose insurers from the collector's own `--list` and the documents flag. It is started without a shell, and nothing about you or the machine is added to it.

## Names, saved copies and periods

- **Short names.** Where space is tight (board rows and headers, Moves, legends, search) the app uses `name_short_en` of a product or insurer when the data has it, and otherwise its own shortening. The product page always shows the full name.
- **Source window.** A rate opens the saved copy it came from. Saved pages are shown as pages; saved API responses (`.json`) and text extracts (`.txt`) are shown as text with the figure marked and scrolled to. If a copy is recorded but cannot be shown, the message says so.
- **Rate periods in charts.** A point is drawn for one period of its own series (half-month, month, or N days, from the insurer's `rate_period` or the product's own) and joined to the next point only when that is at most about 1.3 periods away.
- **Markets colours.** Each small chart colours its own lines (its top six products by current rate) and carries its own legend; further products are counted as "+k more".
- **Documents.** Each row shows an English title that differs from every other row of the product, and a date: when the copy was retrieved, the edition from the data's `edition` field or from the Japanese title, or "date unknown". A document that is listed but not on disk reads "Not downloaded" (or "File missing").
- **Unchecked mark.** Where every rate in a grid block or a column is unchecked, one ○ on the caption or column heading stands for all of them. The key says "not independently re-checked" for a product that has never been re-checked.
- **Saved-copy insurers.** On the Data page an insurer whose collector only re-reads page copies saved by hand shows "Saved copies re-read", not "Updated".

## Package

```bash
npm run pack:mac
```

```bash
npm run pack:win
```

Output goes to `app/dist/` (a `.dmg` and `.zip` on macOS, an NSIS installer and a portable `.exe` on Windows). Builds are unsigned, so macOS Gatekeeper and Windows SmartScreen will warn on first launch. Build the Windows target on Windows; cross-building from a Mac needs Wine. Only `src/` and the runtime libraries (charting, PDF.js, xterm.js, node-pty and the Agent SDK with its `claude` binary) are packaged; `data/`, `sources/` and `scripts/` are never included, so a packaged build needs a data folder that contains `scripts/` for the update run to work.

### Native parts and Windows

The assistant needs two things that are specific to the platform:

- **node-pty** ships prebuilt binaries for macOS (arm64, x64) and Windows (x64, arm64), so nothing is compiled; `npmRebuild` is off in the build config. In a packaged app it is unpacked from the archive (`asarUnpack`), along with `src/chat/bridge.js` and the SDK's binary, because other programs have to run them.
- **The `claude` binary** comes from the SDK's per-platform package (`@anthropic-ai/claude-agent-sdk-darwin-arm64` here, about 350 MB). `npm install` fetches the one for the machine it runs on, so a Windows build must be made on Windows (`npm install` then `npm run pack:win`) to get `claude.exe`; a build made on this Mac contains only the Mac binary.
- Not tried on Windows: the pane's hook commands are written for `cmd.exe` there, node-pty uses ConPTY, and Claude Code on Windows expects Git for Windows to be installed. Treat the assistant on Windows as untested.

## Layout

- `src/main.js` - window, menu, IPC handlers, network block
- `src/dataset.js` - all file reading, confined to the chosen data folder, including the manifest check for the document viewer
- `src/collector.js` - finds Python and runs `scripts/collect_all.py` for the Data page
- `src/translate.js` - starts the translation engine for a manifest-listed document, relays progress, serves and deletes results
- `src/chat/` - the assistant: `terminal.js` (starts Claude Code in a pseudo-terminal), `bridge.js` (run by Claude Code: the MCP server and hooks that call back into the app), `server.js` (local server for the bridge, 127.0.0.1 with a per-launch token), `tools.js` (tool list and argument checks), `guard.js` (which paths may be read), `prompt.js` (its instructions), `sessions.js` (conversation list)
- `src/preload.js` - the only API the page can call
- `src/csv.js` - CSV parser
- `src/renderer/` - the page: `model.js` (data model, English labels), `chart.js`, `views.js` (Leaderboard, Markets, Moves, Chart), `product.js` (product and Data pages), `candidates.js` (Candidates page), `pdfview.js` (embedded PDF viewer), `pdftranslate.js` (JP / EN / side-by-side, Translate, progress and review marks), `chat.js` (assistant pane), `chattools.js` (the assistant's tools, answered from the screen's data), `app.js` (shell), `styles.css`; charts use uPlot from `node_modules`
- `build/` - the app icon: `icon.png` (Windows), `icon.icns` and `icon-mac.png` (macOS), all drawn by `python3 build/make-icon.py`; `icon.svg` is the reference drawing
- `prototype/` - the design prototype the look was chosen from, and screenshots of the rebuilt app; not packaged
- `test/smoke.js` and `test/rank.js` - `npm test`: the parser and loader (and that every net-yield check row finds its point in the model), and on small fixtures the board's rank-change and tie rules, the Over column's tenor, nearest curve tenor and 14-day cut-off, net-yield check matching and single-plan labels, without Electron
- `test/verify.js` - launches the app, walks through the views in `test/steps.json` and saves screenshots: `npx electron test/verify.js .. <output-folder> [dark]` with `IRV_STEPS` set to the contents of `test/steps.json` (`test/chat-steps.json` and `test/chat-doc-steps.json` ask the assistant real questions, so they use your Claude sign-in and plan; `test/pdf-steps.json` exercises the document viewer and the refusal of files outside the manifests; `test/update-steps.json` runs a T&D-only update from the Data page, so it does contact that insurer's site; `IRV_SIZE=1100x800` sets the window size). The harness uses a new, empty settings folder each run; `IRV_KEEP_USER_DATA` reuses a named one, so never point it at the app's real settings folder
- `test/finish-steps.json` - Over column (USD 10Y header and cell title), "=" ties in the history legend, no plan label for a single-plan product, ✓ marks on a net-yield board, As of an earlier day and back, enabling a candidate (it appears on Products and Explore) and moving it back, and the Japan-connection message said once on the Data page. Each step returns `ok: true` in `report.json` when its checks pass. From `app/`: `IRV_STEPS="$(cat test/finish-steps.json)" npx electron test/verify.js .. <output-folder>`; add `dark` after the output folder for dark mode (the main steps too: `IRV_STEPS="$(cat test/steps.json)" npx electron test/verify.js .. <output-folder> dark`)
