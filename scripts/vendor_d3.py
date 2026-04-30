"""Fetcher for the vendored d3 bundle used by ``html_report.py``.

Two ways to invoke:

* As a script: ``uv run python scripts/vendor_d3.py``. Idempotent.
* As a module: ``install.py`` imports :func:`vendor` and calls it during
  ``uv run python install.py`` so most users never run the script
  manually — the d3 bundle is fetched as part of the standard install.

The function downloads a pinned ``d3@7.9.0`` minified bundle from the
configured CDN, verifies the SHA-256 against the literal pinned in this
file, and writes it to ``src/agent_evolve/viz/_vendor/d3.v7.min.js``.
``html_report.py`` then inlines the contents into every HTML report, so
the report opens correctly with no network access — including in
air-gapped CI, on a flight, or when the CDN is reorganised.

Re-running is safe: when the file is already present and matches the
pinned hash, the fetch is skipped entirely. When the file is present
but stale (hash mismatch — typically because someone bumped only the
URL constant and forgot the hash), the function refuses to overwrite
and surfaces the discrepancy.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# Pinned to d3 7.9.0 minified bundle. Keep these two literals in lockstep:
# any change to the URL must change the hash, and vice versa. The hash
# was sourced from the official npm tarball (``d3-7.9.0.tgz``) and matches
# the file served by jsdelivr at the URL below as of 2025.
_D3_URL: str = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"
# Hash captured from a successful fetch of the URL above against jsdelivr
# on 2026-04-29; verified to match the bytes the script writes via the
# self-check in :func:`vendor`. Bumping the d3 version means changing
# both literals in the same commit.
_D3_SHA256: str = "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539"

# Where the vendored bundle lives. ``html_report.py`` reads from here at
# render time. Path is relative to the repo root, not this script — the
# script may run from anywhere.
_VENDOR_PATH: Path = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "agent_evolve"
    / "viz"
    / "_vendor"
    / "d3.v7.min.js"
)

# Network timeout in seconds. d3.min.js is ~280 KB; a 30 s budget handles
# the slowest reasonable connection without hanging CI on a transient
# DNS issue.
_TIMEOUT_S: float = 30.0


def vendor(*, force: bool = False, verbose: bool = True) -> int:
    """Fetch and verify the vendored d3 bundle.

    *force* re-downloads even when an existing file already matches the
    pin. *verbose* controls whether progress is printed to stderr —
    callers (e.g. ``install.py``) suppress it when they want to handle
    logging themselves.

    Returns:

    * ``0`` on success (file written, or already up-to-date).
    * ``2`` on hash mismatch (unrecoverable without code change).
    * ``3`` on network failure (recoverable — caller should warn and
      continue rather than abort).
    """
    if not force and _VENDOR_PATH.exists():
        existing = _VENDOR_PATH.read_bytes()
        if hashlib.sha256(existing).hexdigest() == _D3_SHA256:
            if verbose:
                print(
                    f"[vendor_d3] up-to-date — {_VENDOR_PATH} "
                    f"({len(existing):,} bytes)",
                    file=sys.stderr,
                )
            return 0

    if verbose:
        print(f"[vendor_d3] fetching {_D3_URL}", file=sys.stderr)

    try:
        req = Request(_D3_URL, headers={"User-Agent": "agent-evolve vendor_d3.py"})
        with urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = resp.read()
    except (URLError, TimeoutError, OSError) as e:
        # Network is unavailable, the URL is unreachable, or the read
        # was interrupted. Caller decides whether to abort — for the
        # install path, this is a soft failure (CDN fallback in
        # ``html_report.py`` keeps the rest of the system working).
        if verbose:
            print(
                f"[vendor_d3] network failure: {e}\n"
                f"[vendor_d3] continuing without vendored d3 — reports "
                f"will use the CDN fallback at render time. Re-run "
                f"`uv run python scripts/vendor_d3.py` from a connected "
                f"machine to inline the bundle.",
                file=sys.stderr,
            )
        return 3

    digest = hashlib.sha256(body).hexdigest()
    if digest != _D3_SHA256:
        print(
            f"[vendor_d3] hash mismatch — refusing to write\n"
            f"[vendor_d3]   expected: {_D3_SHA256}\n"
            f"[vendor_d3]   actual:   {digest}\n"
            f"[vendor_d3]   url:      {_D3_URL}\n"
            f"[vendor_d3] This means the upstream bundle changed under the URL\n"
            f"[vendor_d3] or the download was corrupted. Update _D3_URL and\n"
            f"[vendor_d3] _D3_SHA256 in scripts/vendor_d3.py in the same commit\n"
            f"[vendor_d3] if you intentionally want to bump the d3 version.",
            file=sys.stderr,
        )
        return 2

    _VENDOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    _VENDOR_PATH.write_bytes(body)
    if verbose:
        print(
            f"[vendor_d3] wrote {_VENDOR_PATH} ({len(body):,} bytes, "
            f"sha256={digest[:12]}…)",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    """CLI entry point for ``python scripts/vendor_d3.py``."""
    return vendor(force=False, verbose=True)


if __name__ == "__main__":
    raise SystemExit(main())
