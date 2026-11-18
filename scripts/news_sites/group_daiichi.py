"""Dai-ichi Life Group (formerly Dai-ichi Life Holdings): news releases at www.daiichilife-group.com/news/.

The year pages (/news/2026/ ...) are empty shells filled in by JavaScript from one JSON file,
/news/json/news.json: a list of {date "2026年11月12日", datetime, fiscal_year, title, category, company_name,
link_type, url, file_size}. The source url is that JSON file, so one request returns every year.
Releases that concern only the pet-insurance and Benefit One subsidiaries are skipped; items with no link
are skipped; relative urls are made absolute.
"""

import json
import re
import urllib.parse

import common

OTHER_ONLY = {"第一アイペット損害保険", "ベネフィット・ワン"}


def parse(body, source, page_url):
    data = json.loads(body.decode("utf-8-sig") if isinstance(body, bytes) else body)
    out, seen = [], set()
    for row in data:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", row.get("datetime") or "")
        url = (row.get("url") or "").strip()
        title = common.clean(re.sub(r"<[^>]+>", " ", row.get("title") or ""))
        if not (m and url and title):
            continue
        companies = {c.strip() for c in (row.get("company_name") or "").split(",") if c.strip()}
        if companies and companies <= OTHER_ONLY:
            continue
        url = urllib.parse.urldefrag(urllib.parse.urljoin("https://www.daiichilife-group.com/", url))[0]
        if url in seen:
            continue
        seen.add(url)
        out.append({"date": m.group(1), "title_ja": title, "url": url})
    return out
