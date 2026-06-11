# scripts/vendor_sackmann_corpus.py
"""Download the full Sackmann ATP/WTA corpus into data/sources/sackmann_corpus/.

This vendors the REAL Jeff Sackmann corpus (2024-2026 matches + current
rankings) into ``data/sources/sackmann_corpus/`` — a directory kept
DELIBERATELY SEPARATE from ``data/sources/sackmann_snapshot/``. The snapshot
dir holds the small SYNTHETIC, edge-case-bearing TEST fixtures (built by
``data/sources/_internal/build_sackmann_snapshot.py``); the corpus dir holds
the real GitHub data the flag-on real-signal path consumes. Keeping them apart
means re-vendoring the real corpus never clobbers the hermetic test fixtures
(and vice-versa).

Idempotent: overwrites the vendored corpus CSVs with the current GitHub master.
Run whenever the corpus should be refreshed (Sackmann publishes the current
year incrementally, ~weeks behind live).
"""
from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

# (codex fix) `requests` is NOT relied on here — use stdlib urllib so this
# one-shot data step works on a fresh checkout without importing the project
# package (which would pull pandas et al.).
_DEST = Path("data/sources/sackmann_corpus")
_BASE = "https://raw.githubusercontent.com/JeffSackmann"
_FILES = [
    *[f"{tour}_matches_{yr}.csv" for tour in ("atp", "wta") for yr in (2024, 2025, 2026)],
    *[f"{tour}_rankings_current.csv" for tour in ("atp", "wta")],
]


def main() -> int:
    _DEST.mkdir(parents=True, exist_ok=True)
    for fname in _FILES:
        repo = "tennis_atp" if fname.startswith("atp") else "tennis_wta"
        url = f"{_BASE}/{repo}/master/{fname}"
        req = urllib.request.Request(url, headers={"User-Agent": "genesis-sackmann-vendor"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 — public read-only
                text = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"  SKIP {fname} ({exc})")
            continue
        # Normalise to LF and write with ``newline=""`` so Python performs NO
        # newline translation on any host. Upstream Sackmann serves LF; without
        # this, ``write_text`` rewrites every line CRLF on Windows, which both
        # bloats the blob and makes a Linux/macOS re-vendor produce a spurious
        # whole-file diff — defeating the idempotent-refresh goal. (.gitattributes
        # pins ``*.csv text eol=lf`` as a second line of defence in git itself.)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        (_DEST / fname).write_text(text, encoding="utf-8", newline="")
        rows = max(0, text.count("\n") - 1)
        print(f"  wrote {fname} ({rows} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
