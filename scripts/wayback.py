"""Polite Wayback Machine access shared by the collectors' --wayback modes.

Standard library only. Requests carry the generic User-Agent from common.py
and nothing else (no names, emails or contact details).

The archive throttles quick sequences of playback requests: it answers 429,
or 200 with a short "suspected abusive bot traffic" page. Both are treated as
a refusal here, never as content, so a block page is never saved as a capture.
"""

import gzip
import json
import time
import urllib.error
import urllib.parse

import common

CDX = "https://web.archive.org/cdx/search/cdx"
RAW = "https://web.archive.org/web/%sid_/%s"
PLAIN = "https://web.archive.org/web/%s/%s"

GAP = 6.0  # seconds between playback requests
_last = [0.0]

BLOCK_MARKERS = (b"suspected abusive bot traffic", b"Your request is being blocked")


def _wait():
    delay = _last[0] + GAP - time.time()
    if delay > 0:
        time.sleep(delay)
    _last[0] = time.time()


def captures(url, match="exact", collapse="digest", statuses=("200",), filters=()):
    """List captures from the CDX API as dicts (timestamp, original, statuscode, mimetype, digest, length).

    match: exact | prefix | host | domain. statuses=None keeps every status.
    """
    params = [
        ("url", url.replace("https://", "").replace("http://", "")),
        ("output", "json"),
        ("matchType", match),
        ("fl", "timestamp,original,statuscode,mimetype,digest,length"),
        ("limit", "20000"),
    ]
    if collapse:
        params.append(("collapse", collapse))
    params += [("filter", f) for f in filters]
    for attempt in range(4):
        try:
            raw = common.fetch(CDX + "?" + urllib.parse.urlencode(params), pause=2.0, timeout=120)
            break
        except urllib.error.HTTPError as err:
            if err.code != 429:
                raise
            time.sleep(60 * (attempt + 1))
    else:
        raise RuntimeError("CDX refused (rate limit) for %s" % url)
    rows = json.loads(raw) if raw.strip() else []
    if not rows:
        return []
    head = rows[0]
    out = [dict(zip(head, r)) for r in rows[1:]]
    if statuses is not None:
        out = [r for r in out if r["statuscode"] in statuses]
    return out


def fetch(ts, original, tries=6):
    """Fetch one capture. Returns (body, wayback_url, form) or None if the archive keeps refusing.

    The raw (id_) form is tried first; form is "raw" or "rewritten". Raises
    HTTPError for 4xx other than 429 on both forms.
    """
    last_err = None
    for form, template in (("raw", RAW), ("rewritten", PLAIN)):
        url = template % (ts, original)
        for attempt in range(tries):
            _wait()
            try:
                body = common.fetch(url, retries=2, pause=0, timeout=120)
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    time.sleep(60 * (attempt + 1))
                    continue
                last_err = err
                break
            except RuntimeError as err:  # network trouble after retries
                last_err = err
                time.sleep(30)
                continue
            if body[:2] == b"\x1f\x8b":  # some raw captures are stored gzipped
                body = gzip.decompress(body)
            if len(body) < 4000 and any(m in body for m in BLOCK_MARKERS):
                time.sleep(60 * (attempt + 1))
                continue
            return body, url, form
        else:
            return None  # kept refusing
    if isinstance(last_err, urllib.error.HTTPError):
        raise last_err
    return None


def ts_date(ts):
    return "%s-%s-%s" % (ts[:4], ts[4:6], ts[6:8])
