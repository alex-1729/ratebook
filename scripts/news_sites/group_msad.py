"""MS&AD Insurance Group Holdings: /ja/news/news_topics.html and /ja/news/news_info.html.

Each page lists every item on one page (back to 2010 and 2023). An item is <li ... entrydate="2026/11/06">
holding <h3><a href=...> with <span class="date"> and <span class="news_tx">title</span>. The generic reader
puts the date and the file size into the title, so this one reads the markup. Relative links are made absolute.
"""

import re
import urllib.parse

import common


def parse(body, source, page_url):
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    out, seen = [], set()
    for block in re.split(r"<li\b[^>]*\bentrydate=", page)[1:]:
        date = re.match(r'"(\d{4})/(\d{1,2})/(\d{1,2})"', block)
        href = re.search(r'<a\s[^>]*href="([^"]+)"', block)
        title = re.search(r'<span class="news_tx">([\s\S]*?)</span>', block)
        if not (date and href and title):
            continue
        text = common.clean(re.sub(r"<[^>]+>", " ", title.group(1)))
        url = urllib.parse.urldefrag(urllib.parse.urljoin(page_url, href.group(1).replace("&amp;", "&")))[0]
        if text and url not in seen:
            seen.add(url)
            y, m, d = (int(x) for x in date.groups())
            out.append({"date": "%04d-%02d-%02d" % (y, m, d), "title_ja": text, "url": url})
    return out
