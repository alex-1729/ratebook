"""Nippon Life (nissay.co.jp): the news and notice list pages are filled in by JavaScript from one JSON file each
(/kaisha/news/json/index.json and /kaisha/topics/json/index.json). Each file is a list of
{"date": "YYYY-MM-DD", "title": "...[1.8MB]", "category", "link": "/path"} covering many years, so no
archive pages are needed. A file-size marker such as [1.8MB] or ［216KB］ ends some titles and is dropped.
"""
import json
import re
import urllib.parse


def parse(body, source, page_url):
    data = json.loads(body.decode("utf-8", "replace"))
    out, seen = [], set()
    for row in data:
        date, title, link = row.get("date", ""), row.get("title", ""), row.get("link", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not title or not link:
            continue
        title = re.sub(r"\s*[\[［][\d.,]+\s*[KMG]?B[\]］]\s*$", "", title).strip()
        url = urllib.parse.urljoin("https://www.nissay.co.jp/", link)
        if url in seen:
            continue
        seen.add(url)
        out.append({"date": date, "title_ja": title, "url": url})
    return out
