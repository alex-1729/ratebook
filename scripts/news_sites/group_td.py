"""T&D Holdings: news releases at www.td-holdings.co.jp/news/.

The page is an empty shell filled in by the E-IR widget from a JSONP file on the IR vendor's host,
https://ssl4.eir-parts.net/V4Public/eir/TJ8795/ja/ir_material/ir_material_1.js (2017 onward; _2.js holds
the older ones). It is `eolparts_ir_material_1({"group":[{"group_name":"2026年度","item":[{"date":
"2026/11/13 14:07:00","link":...,"title":...,"comment":"H T D F<br/>決"}]}]})`. The comment letters say
which group company (H holdings, T Taiyo, D Daido, F T&D Financial Life). The source url is the JSONP file.
"""

import json
import re

import common


def parse(body, source, page_url):
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    start, end = text.index("{"), text.rindex("}")
    data = json.loads(text[start:end + 1])
    out, seen = [], set()
    for group in data.get("group", []):
        for row in group.get("item", []):
            m = re.match(r"(\d{4})/(\d{2})/(\d{2})", row.get("date") or "")
            url = (row.get("link") or "").strip()
            title = common.clean(re.sub(r"<[^>]+>", " ", row.get("title") or ""))
            if m and url and title and url not in seen:
                seen.add(url)
                out.append({"date": "-".join(m.groups()), "title_ja": title, "url": url})
    return out
