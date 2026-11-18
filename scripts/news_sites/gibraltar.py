"""Gibraltar Life notice lists: /st/about/notice/ (and /<year>/ archives) and /st/important_notices/.

These lists have no dates on the page, only a title linked to a PDF or a detail page. The date is taken from the
PDF file name (20261012.pdf, 2026082702.pdf) or, failing that, from a date in the title such as
"（2026年10月1日更新）". Items with neither (hosho2.pdf, a link to a topic page) are skipped, because the detail
pages sit behind a bot challenge and are not fetched. Each item is a <div class="...__item"> holding one link.
"""
import datetime as dt
import re
import urllib.parse

import common

ITEM = re.compile(r'<div class="[\w-]*(?:noticeList|NoticeList)__item"[^>]*>([\s\S]*?)</div>', re.I)
LINK = re.compile(r'<a\b[^>]*href\s*=\s*"([^"]+)"[^>]*>([\s\S]*?)</a>', re.I)
ZEN = str.maketrans("０１２３４５６７８９", "0123456789")


def _valid(y, m, d):
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def parse(body, source, page_url):
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    page = page[page.find("<main"):page.find("</main>")]
    out, seen = [], set()
    for item in ITEM.finditer(page):
        m = LINK.search(item.group(1))
        if not m:
            continue
        url = urllib.parse.urljoin(page_url, m.group(1).replace("&amp;", "&"))
        title = common.clean(re.sub(r"<[^>]+>", " ", m.group(2))).translate(ZEN)
        date = None
        f = re.match(r"(20\d{2})(\d{2})(\d{2})(?:\d{2})?\.pdf$", url.rsplit("/", 1)[-1])
        if f:
            date = _valid(*f.groups())
        if not date:
            t = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", title)
            if t:
                date = _valid(*t.groups())
        if not date or not title or url in seen:
            continue
        seen.add(url)
        out.append({"date": date, "title_ja": title, "url": url})
    return out
