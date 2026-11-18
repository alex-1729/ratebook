"""Prudential Holdings of Japan: https://www.pru-holding.co.jp/phj/release/press/index.html

One page for every year, newest first. Each item is <dt class="date">2026/11/4</dt> followed by
<dd class="news_text"> [category] <a href="/pdf/PHJpress20261104.pdf">title <img alt="[PDF]"></a></dd>.
The generic reader pairs the first item with the page's "print" link, so this one reads the dt/dd pairs.
"""
import datetime as dt
import re
import urllib.parse

import common

PAIR = re.compile(r'<dt[^>]*class="date"[^>]*>([\s\S]*?)</dt>\s*<dd[^>]*>([\s\S]*?)</dd>', re.I)


def parse(body, source, page_url):
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    out, seen = [], set()
    for m in PAIR.finditer(page):
        d = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", re.sub(r"<[^>]+>", "", m.group(1)))
        a = re.search(r'<a\b[^>]*href\s*=\s*"([^"]+)"[^>]*>([\s\S]*?)</a>', m.group(2), re.I)
        if not (d and a):
            continue
        try:
            date = dt.date(*map(int, d.groups())).isoformat()
        except ValueError:
            continue
        title = common.clean(re.sub(r"<[^>]+>", " ", a.group(2)))
        href = a.group(1).replace("&amp;", "&")
        # The page writes its PDF links as "/pdf/...", but the files are served from the list's own folder
        # (/phj/release/press/pdf/...); the root-relative address returns 404.
        if href.startswith("/pdf/"):
            href = href[1:]
        url = urllib.parse.urljoin(page_url, href)
        if not title or url in seen:
            continue
        seen.add(url)
        out.append({"date": date, "title_ja": title, "url": url})
    return out
