# Vendored third-party assets

This directory contains a pinned, verbatim copy of an external JavaScript
bundle that the agent-evolve HTML report renderer
(`src/agent_evolve/viz/html_report.py`) inlines into every report so the
output is fully self-contained and viewable without network access.

## d3.v7.min.js — D3.js 7.9.0

* **Project:** https://github.com/d3/d3
* **Version:** 7.9.0
* **License:** ISC — see https://github.com/d3/d3/blob/v7.9.0/LICENSE for
  the authoritative license text.
* **Copyright:** © 2010-2023 Mike Bostock and contributors. The
  copyright line is preserved verbatim at the top of the bundled file.
* **Origin:** Fetched from `https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js`
  by `scripts/vendor_d3.py`. The bundle is verified against a SHA-256
  hash pinned in that script before it is written.
* **No modifications:** The bundle is byte-identical to the upstream
  release. The vendoring script refuses to overwrite the file when the
  download does not match the pinned hash.

To refresh or upgrade the bundle (e.g. when bumping the d3 version),
edit both `_D3_URL` and `_D3_SHA256` in `scripts/vendor_d3.py` in the
same commit, then run:

    uv run python install.py

The fetch is idempotent: re-running `install.py` when the file already
matches the pinned hash skips the network call entirely.
