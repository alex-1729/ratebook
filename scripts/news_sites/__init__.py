"""Site-specific readers for news list pages that the generic reader in news.py cannot handle.

A reader is `def parse(body, source, page_url)` returning [{"date": "YYYY-MM-DD", "title_ja": ..., "url": ...}].
`body` is the page as bytes. A source names its reader as "parser": "<module>:<function>".
"""
