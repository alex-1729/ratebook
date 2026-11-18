"""Taiyo Life news releases.

The release list pages (/company/notice/press_article/<year>.html) are filled in by JavaScript from one JSON file,
/wr2/json/release.json, which holds every release since April 2024: {"release": [{"date": "2026年11月13日",
"title": ..., "link": "../pdf/...", "file_size": ...}]}. The page script turns "../" into "/wr2/", so this reader
does the same. Dates are Japanese-style and the day can be zero-padded.
"""

import json
import re
import urllib.parse

DATE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")


def parse(body, source, page_url):
    if isinstance(body, bytes):
        body = body.decode("utf-8-sig", "replace")
    data = json.loads(body)
    items = []
    for row in data.get("release", []):
        m = DATE.search(row.get("date", ""))
        title = (row.get("title") or "").strip()
        link = row.get("link") or ""
        if not (m and title and link):
            continue
        if link.startswith("../"):
            link = "/wr2/" + link[3:]
        items.append({
            "date": "%04d-%02d-%02d" % tuple(int(x) for x in m.groups()),
            "title_ja": title,
            "url": urllib.parse.urljoin(page_url, link),
        })
    return items
