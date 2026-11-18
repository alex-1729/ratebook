"""Download the documents listed in product files, for any insurer.

Each data/products/*.json may list "documents": [{name, title_ja, url}, ...].
This saves every one that is a PDF to sources/<insurer>/docs/<product_id>/
and records it in sources/<insurer>/docs/manifest.csv (URL, size, checksum,
date). The PDFs themselves are not kept in git (see .gitignore); the manifest is.

Usage:
    python3 scripts/fetch_docs.py                      # every product
    python3 scripts/fetch_docs.py --only orx-moonshot,cal-jibun-nenkin-kaigo-plus
    python3 scripts/fetch_docs.py --insurers met,mnl

Re-running is safe: a document already in the manifest with its file on disk
is skipped. A site that refuses the request is reported and left; nothing is
worked around. Requests carry only the public URL and the generic browser
User-Agent from common.py.
"""

import argparse
import csv
import json
import re
import sys
import time

import common

FIELDS = ["product_id", "name", "title_ja", "url", "file", "bytes", "sha256", "retrieved_date"]


def load_manifest(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_manifest(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["product_id"], r["name"], r["file"]))
    # Keep optional columns some manifests carry (via_wayback, wayback_url, capture_timestamp, status).
    fields = FIELDS + [k for row in rows for k in row if k not in FIELDS]
    fields = list(dict.fromkeys(fields))
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) or "" for k in fields})


def have(rows, product_id, name):
    for row in rows:
        if row["product_id"] == product_id and row["name"] == name and row.get("file"):
            if (common.ROOT / row["file"]).exists():
                return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="comma-separated product ids")
    parser.add_argument("--insurers", default="", help="comma-separated insurer ids")
    args = parser.parse_args()
    only = {x for x in args.only.split(",") if x}
    insurers = {x for x in args.insurers.split(",") if x}

    products = []
    for path in sorted((common.DATA / "products").glob("*.json")):
        product = json.loads(path.read_text(encoding="utf-8"))
        if only and product["id"] not in only:
            continue
        if insurers and product.get("insurer_id") not in insurers:
            continue
        products.append(product)

    saved = skipped = failed = not_pdf = 0
    total_bytes = 0
    by_insurer = {}
    for product in products:
        by_insurer.setdefault(product["insurer_id"], []).append(product)

    for insurer, group in sorted(by_insurer.items()):
        manifest_path = common.SOURCES / insurer / "docs" / "manifest.csv"
        rows = load_manifest(manifest_path)
        changed = False
        for product in group:
            for doc in product.get("documents") or []:
                url = doc.get("url") or ""
                name = re.sub(r"[^A-Za-z0-9_.-]+", "_", doc.get("name") or "document")
                if not url.lower().startswith("http"):
                    continue
                if have(rows, product["id"], name):
                    skipped += 1
                    continue
                try:
                    body = common.fetch(url, retries=2, timeout=120)
                except Exception as exc:  # a refusal or timeout: report and move on
                    failed += 1
                    print("FAILED   %s %s: %s  (%s)" % (product["id"], name, exc, url), flush=True)
                    continue
                if not body.startswith(b"%PDF"):
                    not_pdf += 1
                    print("NOT PDF  %s %s  (%s)" % (product["id"], name, url), flush=True)
                    continue
                path = common.save_snapshot(common.SOURCES / insurer / "docs" / product["id"], name, "pdf", body)
                rows.append(
                    {
                        "product_id": product["id"],
                        "name": name,
                        "title_ja": doc.get("title_ja", ""),
                        "url": url,
                        "file": common.rel(path),
                        "bytes": str(len(body)),
                        "sha256": common.sha256(body),
                        "retrieved_date": common.today(),
                    }
                )
                changed = True
                saved += 1
                total_bytes += len(body)
                print("saved    %s %s  %.1f MB" % (product["id"], name, len(body) / 1e6), flush=True)
                time.sleep(1.0)
        if changed:
            write_manifest(manifest_path, rows)

    print(
        "done: %d saved (%.0f MB), %d already on disk, %d not a PDF, %d failed"
        % (saved, total_bytes / 1e6, skipped, not_pdf, failed)
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
