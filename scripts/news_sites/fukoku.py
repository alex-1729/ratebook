"""Fukoku Life news releases and notices (/about/news/, /about/information/).

Each page is one long dated list of every year back to 2010 (the year selector only hides rows), so there are no
archive pages. The generic reader handles the rows but keeps only the first row for a link, and a few
announcements (for example the annual general meeting minutes) link to one shared page every year. So each link
is first made unique with a counter, the generic reader runs, and then links that really occur on more than one
row get the row's date as a ?d= query (so each year's row is its own item) while the others get their plain link back.
"""

import collections
import re

HREF = re.compile(r'(href\s*=\s*")([^"#]+)(")', re.I)


def parse(body, source, page_url):
    import news  # the collector; imported late because it is the program that loads this module

    html = news.decode(body, source)
    counter = iter(range(1, 10 ** 6))
    html = HREF.sub(lambda m: m.group(1) + m.group(2) + ("&" if "?" in m.group(2) else "?") + "_n=%d" % next(counter) + m.group(3), html)
    items = news.parse_auto(html, page_url)
    for item in items:
        item["url"] = re.sub(r"[?&]_n=\d+$", "", item["url"])
    count = collections.Counter(i["url"] for i in items)
    seen = collections.Counter()
    for item in items:
        if count[item["url"]] > 1:
            item["url"] += "?d=" + item["date"]
            seen[item["url"]] += 1
            if seen[item["url"]] > 1:  # two rows on the same day for one page
                item["url"] += "&n=%d" % seen[item["url"]]
    return items
