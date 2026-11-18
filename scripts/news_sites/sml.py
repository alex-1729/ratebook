"""Sumitomo Life: /news/newsrelease/<fiscal year>/ and /news/notice/<fiscal year>/.

Items are <li class="rt_bn_news_list"> with <time datetime="YYYY-MM-DD"> and a link whose title sits in
<span class="rt_cf_n_title"> (line breaks as <br>). The page shows 20 items and links to the next ones as
?rt_bn_news_list_skip=20, 40, ...; this reader follows those links itself, so the source needs no page_url.
Links to standing pages get "?news=<date>" added so each announcement keeps its own address.
The generic reader would also pick up the logo and the "latest" side blocks.
"""

import re
import urllib.parse

import common


def one_page(page, page_url):
    out = []
    for block in re.split(r'<li class="rt_bn_news_list">', page)[1:]:  # tag <li>s nest inside, so split
        date = re.search(r'datetime="(\d{4}-\d{2}-\d{2})"', block)
        href = re.search(r'<a\s[^>]*href="([^"]+)"', block)
        title = re.search(r'<span class="__txt rt_cf_n_title">([\s\S]*?)</span>', block)
        if not (date and href and title):
            continue
        text = common.clean(re.sub(r"<[^>]+>", "", title.group(1)))
        url = urllib.parse.urldefrag(urllib.parse.urljoin(page_url, href.group(1).replace("&amp;", "&")))[0]
        if text and "/news/detail/" not in url and not url.lower().endswith(".pdf") and "sumitomolife.co.jp" in url:
            # A standing page (quarterly reports, responsible-investment report) is announced again each
            # period: tell the entries apart, since the collector keeps one item per address.
            url += ("&" if "?" in url else "?") + "news=" + date.group(1)
        if text:
            out.append({"date": date.group(1), "title_ja": text, "url": url})
    return out


def parse(body, source, page_url):
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    out = one_page(page, page_url)
    base = page_url.split("?")[0]
    done, queue = {0}, sorted({int(n) for n in re.findall(r"rt_bn_news_list_skip=(\d+)", page)})
    while queue:
        skip = queue.pop(0)
        if skip in done:
            continue
        done.add(skip)
        try:
            more = common.fetch("%s?rt_bn_news_list_skip=%d" % (base, skip)).decode("utf-8", "replace")
        except Exception:
            break
        out += one_page(more, base)
        queue = sorted(set(queue) | {int(n) for n in re.findall(r"rt_bn_news_list_skip=(\d+)", more)} - done)
    seen, uniq = set(), []
    for item in out:
        if item["url"] not in seen:
            seen.add(item["url"])
            uniq.append(item)
    return uniq
