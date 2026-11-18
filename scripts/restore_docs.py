"""Put back the PDFs that are not kept in git, from what git does record.

The PDFs under sources/ are ignored by git (copyrighted, large), so a fresh
clone has none of them. This downloads them again:

1. Product documents. Every row of sources/<insurer>/docs/manifest.csv gives
   the URL, the path, the size and the SHA-256 of one PDF. The file is
   downloaded again (from the recorded Wayback capture when the row came from
   one) and saved only if its checksum matches the manifest.
2. Distributors' product lists. sources/<insurer>/distributors/manifest.csv
   has the same columns for the PDFs saved from banks' own sites as evidence
   of who sells a product, and they are restored and checked the same way.
3. News-release PDFs under sources/<insurer>/pages/. These have no manifest:
   a product file names one as {"url": ..., "snapshot": ...}. There is no
   checksum to compare with, so they are saved as "unverified". A URL that
   does not return a PDF is reported and left.

Usage:
    python3 scripts/restore_docs.py --dry-run           # what is missing; no requests
    python3 scripts/restore_docs.py                     # restore everything missing
    python3 scripts/restore_docs.py --insurers nwl,dfl
    python3 scripts/restore_docs.py --only orx-moonshot
    python3 scripts/restore_docs.py --no-releases       # manifests only

Re-running is safe. A file already on disk is never overwritten: it is
checked against the manifest and reported if it differs. A download whose
checksum differs from the manifest (the insurer has replaced the document) is
not saved; scripts/fetch_docs.py fetches the current version as a new, dated
row. The manifests and product files are never changed.

Requests carry only the public URL and the generic browser User-Agent from
common.py. No cookies, names, emails or contact details are sent.
"""

import argparse
import csv
import json
import os
import re
import sys

import common
import wayback

RELEASE_PATH = re.compile(r"^sources/[^/]+/pages/[^/]+\.pdf$")


class ArchiveRefusing(RuntimeError):
    pass


def manifest_jobs():
    """One job per manifest row that names a file."""
    jobs = []
    manifests = sorted(common.SOURCES.glob("*/docs/manifest.csv")) + sorted(
        common.SOURCES.glob("*/distributors/manifest.csv")
    )
    for manifest in manifests:
        insurer = manifest.parent.parent.name
        kind = "document" if manifest.parent.name == "docs" else "distributor list"
        with manifest.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if not row.get("file") or not row.get("url"):
                    continue
                jobs.append(
                    {
                        "kind": kind,
                        "insurer": insurer,
                        "product_id": row.get("product_id", ""),
                        "file": row["file"],
                        "url": row["url"],
                        "sha256": (row.get("sha256") or "").lower(),
                        "bytes": int(row["bytes"]) if (row.get("bytes") or "").isdigit() else 0,
                        "capture": row.get("capture_timestamp") or "" if row.get("via_wayback") == "true" else "",
                    }
                )
    return jobs


def release_jobs():
    """One job per {"url", "snapshot"} in a product file whose snapshot is a PDF under sources/<insurer>/pages/."""
    found = {}

    def walk(node, product_id):
        if isinstance(node, dict):
            snapshot, url = node.get("snapshot"), node.get("url")
            if isinstance(snapshot, str) and isinstance(url, str) and RELEASE_PATH.match(snapshot):
                found.setdefault(
                    snapshot,
                    {
                        "kind": "release",
                        "insurer": snapshot.split("/")[1],
                        "product_id": product_id,
                        "file": snapshot,
                        "url": url,
                        "sha256": "",
                        "bytes": 0,
                        "capture": "",
                    },
                )
            for value in node.values():
                walk(value, product_id)
        elif isinstance(node, list):
            for value in node:
                walk(value, product_id)

    for path in sorted((common.DATA / "products").glob("*.json")):
        product = json.loads(path.read_text(encoding="utf-8"))
        walk(product, product.get("id", path.stem))
    return [found[key] for key in sorted(found)]


def target(job):
    """The job's path on disk, or None if the recorded path would leave sources/."""
    path = (common.ROOT / job["file"]).resolve()
    try:
        path.relative_to(common.SOURCES.resolve())
    except ValueError:
        return None
    return path


def download(job):
    if job["capture"]:
        got = wayback.fetch(job["capture"], job["url"], tries=2)
        if got is None:
            raise ArchiveRefusing("the Wayback Machine is refusing requests for now; run again later")
        return got[0]
    return common.fetch(job["url"], retries=2, timeout=120)


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(body)
    os.replace(part, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--insurers", default="", help="comma-separated insurer ids")
    parser.add_argument("--only", default="", help="comma-separated product ids")
    parser.add_argument("--dry-run", action="store_true", help="report what is missing without downloading")
    parser.add_argument("--no-releases", action="store_true", help="skip the news-release PDFs under pages/")
    args = parser.parse_args()
    insurers = {x for x in args.insurers.split(",") if x}
    only = {x for x in args.only.split(",") if x}

    jobs = manifest_jobs() + ([] if args.no_releases else release_jobs())
    jobs = [j for j in jobs if (not insurers or j["insurer"] in insurers) and (not only or j["product_id"] in only)]
    # Wayback captures last: the archive throttles, and each refusal is waited out.
    jobs.sort(key=lambda j: bool(j["capture"]))

    count = dict.fromkeys(["ok", "differs", "missing", "restored", "unverified", "changed", "not_pdf", "failed"], 0)
    restored_bytes = missing_bytes = 0
    archive_refusing = False  # after one refusal the other Wayback rows are not tried in this run

    def say(tag, job, note=""):
        print("%-10s %s%s" % (tag, job["file"], "  (%s)" % note if note else ""), flush=True)

    for job in jobs:
        path = target(job)
        if path is None:
            count["failed"] += 1
            say("FAILED", job, "path is outside sources/")
            continue
        if path.exists():
            if job["sha256"] and common.sha256(path.read_bytes()) != job["sha256"]:
                count["differs"] += 1
                say("DIFFERS", job, "on disk, but not the file the manifest describes; left as it is")
            else:
                count["ok"] += 1
            continue
        if args.dry_run:
            count["missing"] += 1
            missing_bytes += job["bytes"]
            say("missing", job, "%.1f MB" % (job["bytes"] / 1e6) if job["bytes"] else "")
            continue
        if job["capture"] and archive_refusing:
            count["failed"] += 1
            say("FAILED", job, "not tried: the Wayback Machine is refusing requests for now; run again later")
            continue
        try:
            body = download(job)
        except Exception as exc:  # a refusal, a removed page or a timeout: report and move on
            count["failed"] += 1
            archive_refusing = archive_refusing or isinstance(exc, ArchiveRefusing)
            say("FAILED", job, "%s; %s" % (exc, job["url"]))
            continue
        if job["sha256"]:
            if common.sha256(body) != job["sha256"]:
                count["changed"] += 1
                say("CHANGED", job, "the download is not the file in the manifest; not saved; %s" % job["url"])
                continue
            write(path, body)
            count["restored"] += 1
            say("restored", job, "%.1f MB" % (len(body) / 1e6))
        elif not body.startswith(b"%PDF"):
            count["not_pdf"] += 1
            say("NOT PDF", job, "the recorded URL is a web page, not the PDF; %s" % job["url"])
            continue
        else:
            write(path, body)
            count["unverified"] += 1
            say("unverified", job, "%.1f MB, no checksum on record" % (len(body) / 1e6))
        restored_bytes += len(body)

    if args.dry_run:
        print(
            "dry run: %d on disk and correct, %d on disk but different, %d missing (%.0f MB listed in the manifests)"
            % (count["ok"], count["differs"], count["missing"], missing_bytes / 1e6)
        )
        return 0
    print(
        "done: %d restored and checksum-verified, %d restored unverified (%.0f MB), %d already on disk, "
        "%d on disk but different, %d changed at the source, %d not a PDF, %d failed"
        % (
            count["restored"],
            count["unverified"],
            restored_bytes / 1e6,
            count["ok"],
            count["differs"],
            count["changed"],
            count["not_pdf"],
            count["failed"],
        )
    )
    return 0 if not (count["changed"] or count["not_pdf"] or count["failed"] or count["differs"]) else 2


if __name__ == "__main__":
    sys.exit(main())
