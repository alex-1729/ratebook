# About this copy

This is a snapshot of the project taken on 2026-11-18, without its version history.

Left out:

- `sources/`: the saved copies of insurers' and banks' web pages and documents that the data was read from. They are other companies' material, so they are not passed on. Only the `manifest.csv` files are kept, which list each document's URL, size and checksum.
- `research_notes/`: working notes on how the data was gathered and checked. The README, one report and one product file mention files in it; those are not in this copy.

What this means when you use it:

- The app runs from `data/` as normal (`cd app && npm install && npm start`).
- Clicking a rate to open its source, and the Documents tab, find no saved copy. Each rate row in `data/rates/` still carries the original `source_url`.
- `python3 scripts/restore_docs.py` downloads the documents again from the manifests. The collectors in `scripts/` fetch the insurers' pages again and rebuild the saved copies as they run.
