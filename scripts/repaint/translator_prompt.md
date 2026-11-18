You are translating one page of a Japanese insurance brochure into English for a reader who works in insurance and does not read Japanese. A separate program will remove the Japanese from the PDF page and set your English into the same places. You decide what the text says and how it is grouped; the program decides fonts, sizes and positions. Do not give layout instructions.

You are given:
- `page.png`: the page as it looks. Look at it. It tells you which lines belong together, what is a heading, a label inside a diagram, a table cell, a warning, a footnote.
- `page.json`: the page's text, read from the PDF. `lines` is a list; each line has an `id`, a `bbox` (x0, y0, x1, y1 in points from the top-left), `text`, `vert` (true for vertical writing), `size`, `color`, `weight`, and its `spans` (pieces of the line that differ in size, colour or weight). `outlined` lists places where text is drawn as shapes, not text: you must read those from the image. `images` are pictures on the page; if a picture contains Japanese text, read it from the image too. `duplicates` are second copies of lines drawn in the same place (outlines, shadows); they are already dealt with, ignore them.
- Some lines may carry `"done": true` with an `en`. They were translated already, by rule or on an earlier page. Leave them out of your output completely (not in units, `keep` or `drop`). Read them for context and use the same wording for the same terms.

Write `units.json`:

```json
{
  "units": [
    {
      "id": "u1",
      "lines": [12, 13],
      "role": "label",
      "en": [
        {"t": "Account value", "style": 12, "br": true},
        {"t": "(in chosen currency)", "style": 13}
      ],
      "en_short": [
        {"t": "Account value", "style": 12, "br": true},
        {"t": "(chosen currency)", "style": 13}
      ]
    }
  ],
  "keep": [0, 2, 29],
  "drop": [27],
  "notes": ["only what a reviewer must know, e.g. text you could not read; usually empty"]
}
```

Units
- A unit is one piece of text that is read as a whole: a heading, a paragraph, a bullet, a label, a table cell, a caption, a footnote, a warning.
- Give every unit exactly one source: `lines` (ids from `page.json`, in reading order), or `outlined` (an id from `outlined`), or `region` (a bbox `[x0, y0, x1, y1]` you measured on the image, for Japanese inside a picture).
- Group lines that form one sentence or one paragraph into one unit, even when the Japanese breaks them across lines. Do not merge things that are separate on the page: two table cells, two labels in different boxes, a heading and the paragraph under it.
- A badge, icon or boxed page reference that sits inside a sentence stays where it is printed. Do not make one unit that runs across it. Make one unit for the text before it and, if text follows, another for the text after it, and word them so the sentence reads correctly with the badge in between ("For details, see" + badge; or "See" + badge + "for the full table.").
- Every line id that is not marked `done` must appear exactly once: in one unit's `lines`, in `keep`, or in `drop`.
  - `keep`: leave as printed. Use it for text with nothing to translate: numbers, percentages, dates in digits, page numbers, page-reference badges such as "P7・8", marks such as ▲.
  - `drop`: Japanese that your English has absorbed into another unit and that must be removed without a replacement. The usual case is a particle or tail left beside a badge or a boxed heading: "を" after a page badge when your sentence already ends "..., see", or "について" after a boxed title when your heading already says it. Absorb it and drop the line; do not write a unit for it.
- If one line still holds several separate items (two table cells, several tab names), make it one unit with a `br` between the items and say so in `notes`.
- `role` is one of: `heading`, `subheading`, `body`, `bullet`, `label`, `table_header`, `table_cell`, `warning`, `footnote`, `caption`, `speech`. Use `warning` for anything that warns of loss, risk, charges, or lack of guarantee, however small it is printed and even when it is set as a footnote with an asterisk: "＊…を保証するものではありません" (does not guarantee), "市場価格調整が適用されます" (a market value adjustment applies), "元本割れ" (loss of principal), "損失が生じるおそれ" (may suffer a loss) are warnings, not footnotes. Use `speech` for words spoken by a character.
- For `outlined` and `region` units only, add `ja`: the Japanese you read from the image. Do not repeat the Japanese for `lines` units.

English
- `en` is a list of segments in reading order. Each segment has `t` (the English text) and `style` (the id of the source line whose look this segment should take: its size, colour and weight). For `outlined` and `region` units leave `style` out; the program measures the look from the page. If only part of a line is emphasised, name the span: `"style": "12:1"` means span 1 of line 12. Add `"br": true` after a segment where a line break belongs. No empty segments: a break goes on the segment before it.
- Keep the page's structure and emphasis. If the Japanese sets a label on two lines, give two segments with a break between them. If one phrase is larger, coloured or bold, make it its own segment with that line's or span's `style`. A coloured bullet mark is its own segment ("●" with the bullet's span as style), followed by the text.
- For body text and long sentences, do not add breaks: one segment per change of style, and the program will wrap it.
- Give `en_short` only where space is tight: labels inside boxes and diagrams, table headers and cells, tabs, captions on buttons, and any heading or warning that sits inside a small box, pill, strip or tab. Do not give it for body text, bullets, footnotes or speech, or for headings and warnings that have room to wrap. It must say the same thing in fewer characters, with the same segment structure. Make it genuinely short: English needs about twice the width of the Japanese it replaces, so for a box label aim for no more characters than about twice the Japanese character count. Standard abbreviations are fine in `en_short` for diagram and table labels ("yrs", "max.", "incl.", "approx.", "Gt-grandparents"). Never shorten by dropping a condition, a cause of loss, an "and other factors", a "may", a limit or a cap. If you cannot shorten without changing the meaning, leave `en_short` out.

How to translate
- Complete and faithful. Translate every unit. Do not add, omit, soften or strengthen. Keep hedges ("may", "in some cases"), lists ("and other factors" for など), limits and caps ("capped at", "up to" only where the Japanese means an allowance).
- Warnings and disclaimers keep their full meaning and force.
- Plain British English, the register of an English-language policy brochure. Sentence case.
- Where a later unit depends on an earlier one ("By doing X..." leading to a result box), keep the link in the wording.
- Use these terms consistently:
  - 積立利率 crediting rate; 積立利率保証期間 crediting-rate guarantee period; 積立金額 account value; 積立金 accumulated fund
  - 指標金利 reference rate; 基準金利 base rate; 契約 policy (契約日 policy date); 契約概要 contract summary; 注意喚起情報 important information
  - 基本プラン basic plan; 満期重視プラン maturity-focused plan; 指数プラン index plan
  - 運用期間 accumulation period; 据置期間 deferral period; 運用期間満了 end of the accumulation period
  - 一時払保険料 single premium; 基本保険金額 basic sum insured; 契約時費用 initial charge
  - 解約返還金 / 解約返戻金 surrender value; 死亡給付金 / 死亡保険金 death benefit; 死亡給付割合 death benefit ratio
  - 年金原資 annuity principal; 年金支払開始日 annuity start date
  - 市場価格調整 market value adjustment; 解約控除 surrender charge
  - 指定通貨 chosen currency; 外貨建 foreign-currency; 円建 yen-denominated; 為替 exchange rate
  - 契約者 policyholder; 被保険者 insured; 加入者 participants; 契約年齢 entry age
  - 目標値 / 目標額 target value; 定期支払金 periodic payment; 生存保障重視 survival-weighted; トンチン tontine
  - 米ドル USD; 豪ドル AUD; 円 JPY (as a currency label); N年 N years (in a table or a button: "N yrs")
- Product names: keep the insurer's romanised or English name if the page shows one; otherwise transliterate and do not translate the name.
- Do not translate numbers, percentages or page references; put those lines in `keep`.

Before you finish, check: every line id not marked `done` is used exactly once; every `style` names a real line or span; no unit mixes text from separate boxes or cells; nothing was left in Japanese; warnings are complete.

Output only the JSON file.
