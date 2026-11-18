"""Prudential Life Japan: https://www.prudential.co.jp/news/ (and /news/<year> archives).

One list for every news item and notice. Each item is <article class="news__article"> holding a link, a
<time class="news__date" datetime="YYYY-MM-DD hh:mm"> and <h3 class="news__title"> (a PDF size note sits in an <i>).
The generic reader would pick up the date text as part of the title, so this one reads the structure.
"""
import re
import urllib.parse

import common

ARTICLE = re.compile(r'<article[^>]*class="news__article"[^>]*>([\s\S]*?)</article>', re.I)


def parse(body, source, page_url):
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    out, seen = [], set()
    for m in ARTICLE.finditer(page):
        block = m.group(1)
        href = re.search(r'href\s*=\s*"([^"]+)"', block)
        when = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', block)
        title = re.search(r'<h3[^>]*class="news__title"[^>]*>([\s\S]*?)</h3>', block)
        if not (href and when and title):
            continue
        text = re.sub(r"<i\b[^>]*>[\s\S]*?</i>", "", title.group(1))
        text = common.clean(re.sub(r"<[^>]+>", " ", text))
        url = urllib.parse.urljoin(page_url, href.group(1).replace("&amp;", "&"))
        if not text:
            continue
        if not re.search(r"\.pdf$|/(?:news|press)/\d{4}/\d+$", url):
            # An evergreen page (for example /contractor/relief/relief07.html) is announced again each year. The
            # collector keys items by URL, so a harmless ?d=date query keeps each year's announcement as its own item.
            url += "?d=" + when.group(1)
        if url in seen:
            continue
        seen.add(url)
        out.append({"date": when.group(1), "title_ja": text, "url": url})
    return out


LINK = re.compile(r'<a\b[^>]*href\s*=\s*"([^"]+\.pdf)"[^>]*>([\s\S]*?)</a>', re.I)


def parse_info(body, source, page_url):
    """The dedicated page /info_26/ (apology, administrative action, customer compensation): a single page of
    links to PDFs. The date is the 8 digits at the start of the PDF file name (20261019.pdf)."""
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    page = page[page.find("<main"):page.find("</main>")]
    out, seen = [], set()
    for m in LINK.finditer(page):
        name = m.group(1).rsplit("/", 1)[-1]
        d = re.match(r"(\d{4})(\d{2})(\d{2})", name)
        title = common.clean(re.sub(r"<[^>]+>", " ", m.group(2)))
        url = urllib.parse.urljoin(page_url, m.group(1))
        if not d or not title or url in seen:
            continue
        seen.add(url)
        out.append({"date": "-".join(d.groups()), "title_ja": title, "url": url})
    return out
