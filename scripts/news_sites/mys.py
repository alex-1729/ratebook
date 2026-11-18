"""Meiji Yasuda Life (meijiyasuda.co.jp): the news release list is a page per fiscal year
(/profile/news/release/<fy>/index.html) showing only 20 items, with further pages index_02.html, index_03.html ...
named in a pagination block (`c-pagination__item`). Each item is
`<a class="p-news-list__item" href=...><div class="p-news-list__item-date">2025/03/28</div>
<div class="p-news-list__item-textarea"><p>title</p><span>(PDF 604KB)</span></div></a>`.
parse() reads the page it is given and follows the pagination links itself (extra requests through common.fetch).
The notice page (/profile/news/topics/) is a single page read by the same function.
"""
import html
import re
import urllib.parse

import common

ITEM = re.compile(r'<a\b[^>]*class="p-news-list__item"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>|'
                  r'<a\b[^>]*href="([^"]+)"[^>]*class="p-news-list__item"[^>]*>([\s\S]*?)</a>', re.I)
DATE = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})")
PAGER = re.compile(r'class="c-pagination__item"[^>]*href="([^"]+)"|href="([^"]+)"[^>]*class="c-pagination__item"')


def items(text, page_url):
    out = []
    for m in ITEM.finditer(text):
        href, inner = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        d = DATE.search(inner)
        p = re.search(r"<p\b[^>]*>([\s\S]*?)</p>", inner)
        if not d or not p:
            continue
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", p.group(1)))).strip()
        url = urllib.parse.urldefrag(urllib.parse.urljoin(page_url, html.unescape(href)))[0]
        if len(title) >= 4:
            out.append({"date": "%s-%02d-%02d" % (d.group(1), int(d.group(2)), int(d.group(3))),
                        "title_ja": title, "url": url})
    return out


def parse(body, source, page_url):
    text = body.decode("utf-8", "replace")
    out, seen_pages, queue = [], {page_url}, [(text, page_url)]
    while queue:
        text, url = queue.pop(0)
        out += items(text, url)
        for m in PAGER.finditer(text):
            nxt = urllib.parse.urljoin(url, html.unescape(m.group(1) or m.group(2)))
            if nxt not in seen_pages and "/index_" in nxt and len(seen_pages) < 40:
                seen_pages.add(nxt)
                queue.append((common.decode(common.fetch(nxt, retries=2)), nxt))
    seen, uniq = set(), []
    for i in out:
        if i["url"] not in seen:
            seen.add(i["url"])
            uniq.append(i)
    return uniq
