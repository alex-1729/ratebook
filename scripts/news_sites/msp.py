"""Mitsui Sumitomo Primary Life: /news/info/<fiscal year>/ and /news/ir/<fiscal year>/.

Each item is <article class="news__article"><a href=...> with <time datetime="YYYY-MM-DD"> and an
<h3 class="news__title"> whose text is followed by a file-size <i> ("[PDF:1.2MB]"). The generic reader
puts the date and category into the title, so this one reads the markup directly. /news/info/ and /news/ir/ only forward to the latest year (data-url on <body>); the
reader follows that.
"""

import re
import urllib.parse

import common


def parse(body, source, page_url):
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    redirect = re.search(r'js-news-info-redirect"[^>]*data-url="([^"]+)"', page)
    if redirect:  # /news/info/ itself only forwards to the latest year page
        page_url = urllib.parse.urljoin(page_url, redirect.group(1))
        page = common.fetch(page_url).decode("utf-8", "replace")
    out, seen = [], set()
    for m in re.finditer(r'<article class="news__article">([\s\S]*?)</article>', page):
        block = m.group(1)
        href = re.search(r'<a\s[^>]*href="([^"]+)"', block)
        date = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})"', block)
        title = re.search(r'<h3 class="news__title">([\s\S]*?)</h3>', block)
        if not (href and date and title):
            continue
        text = re.sub(r"<i\b[^>]*>[\s\S]*?</i>", "", title.group(1))
        text = common.clean(re.sub(r"<[^>]+>", " ", text))
        url = urllib.parse.urldefrag(urllib.parse.urljoin(page_url, href.group(1).replace("&amp;", "&")))[0]
        if not url.lower().endswith(".pdf") and not re.search(r"/news/(info|ir)/\d{4}/", url):
            # A standing page (e.g. the disclosure booklet page) is announced again every year: tell the
            # entries apart, since the collector keeps one item per address.
            url += ("&" if "?" in url else "?") + "news=" + date.group(1)
        if text and url not in seen:
            seen.add(url)
            out.append({"date": date.group(1), "title_ja": text, "url": url})
    return out
