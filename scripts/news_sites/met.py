"""MetLife Japan (metlife.co.jp): /about/press/ and /about/info/ are single long pages (back to 2014 and 2010),
each entry a paragraph `<p><b>2026.11.13</b> <a href="...">Headline<br> summary...</a></p>` with the date in
loose form (2026.9.29). The link text of a press release continues after a <br> with a one-line summary; only the
headline before the <br> is kept as the title. Entries without a link are skipped.
"""
import datetime as dt
import html
import re
import urllib.parse

ENTRY = re.compile(r"<b>\s*(20\d{2})\s*[.年/]\s*(\d{1,2})\s*[.月/]\s*(\d{1,2})\s*日?\s*</b>\s*(?:&nbsp;|\s)*(?:<[^>]*>\s*)*?<a\b([^>]*)>([\s\S]*?)</a>", re.I)
HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)


def parse(body, source, page_url):
    text = body.decode("utf-8", "replace")
    out, seen = [], set()
    for m in ENTRY.finditer(text):
        try:
            date = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            continue
        h = HREF.search(m.group(4))
        if not h:
            continue
        head = re.split(r"<br\s*/?>", m.group(5), maxsplit=1, flags=re.I)[0]
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", head))).replace("　", " ").strip()
        url = urllib.parse.urldefrag(urllib.parse.urljoin(page_url, html.unescape(h.group(1)).strip()))[0]
        if len(title) < 4 or url in seen:
            continue
        seen.add(url)
        out.append({"date": date, "title_ja": title, "url": url})
    return out
