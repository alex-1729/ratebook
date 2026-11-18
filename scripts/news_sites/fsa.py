"""Financial Services Agency (fsa.go.jp) press-release lists.

One page per administrative year (July-June): /news/r{N}_news_menu.html, N = Reiwa year of the July start.
Each item is <li class="news-item" data-type="..."><p class="news-date">令和８年６月30日</p> ...
<p class="news-title"><a href="/news/...">title</a></p></li>. Dates are in the Japanese era with full-width
digits, which the generic reader cannot read. The sector tag (data-type) is not stored. Standing pages that
the list announces repeatedly (no date in the URL) get a "?d=YYYY-MM-DD" query (the page ignores it) so each announcement is its own item.
"""
import html
import re
import urllib.parse

DATE = re.compile(r"(令和|平成)\s*(元|\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日")
ZEN = str.maketrans("０１２３４５６７８９", "0123456789")
BASE = {"令和": 2018, "平成": 1988}


def parse(body, source, page_url):
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    out = []
    for chunk in text.split('<li class="news-item"')[1:]:
        d = DATE.search(chunk.translate(ZEN))
        a = re.search(r'<p class="news-title">\s*<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', chunk)
        if not d or not a:
            continue
        year = BASE[d.group(1)] + (1 if d.group(2) == "元" else int(d.group(2)))
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", a.group(2)))).strip()
        date = "%04d-%02d-%02d" % (year, int(d.group(3)), int(d.group(4)))
        url = urllib.parse.urljoin(page_url, html.unescape(a.group(1)))
        stamp = date.replace("-", "")
        if stamp not in url and stamp[2:] not in url and "?" not in url:
            # a standing page announced again and again (earthquake page, personnel changes): keep each
            # announcement as its own item by giving it a harmless "?d=" date query
            url += "?d=" + date
        out.append({"date": date, "title_ja": title, "url": url})
    return out
