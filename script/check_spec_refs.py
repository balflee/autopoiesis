"""Spec-document reference checker.

Walks the repo and verifies every reference to ``PRD.md`` / ``TECHNICAL_PLAN.md``
/ ``DEV_FRAMEWORK.md`` points at an actually-resolvable path. Catches drift
from the 2026-05-25 migration when those files moved from above-``code/`` into
``code/docs/``.

Exit codes:
  0 — all references resolved (or only fuzzy/text-only refs found)
  1 — at least one load-bearing reference points at a non-existent path
  2 — script itself crashed (file I/O error, etc.)

Run from repo root:
  py script/check_spec_refs.py
  py script/check_spec_refs.py --strict   # also flag bare 'PRD.md' refs that
                                          # should probably be 'docs/PRD.md'

Add to pre-commit / CI to prevent future regressions.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The 3 canonical spec docs we track. Order matters: longer names first so
# regex doesn't shadow.
SPEC_DOCS = ("DEV_FRAMEWORK.md", "TECHNICAL_PLAN.md", "PRD.md")

# Where the spec docs ACTUALLY live post-2026-05-25 migration. The checker
# uses this to confirm a path is reachable.
CANONICAL_DIR = "docs"

# Directories to skip (binary, generated, archive).
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", "out", "cache",
    "broadcast", ".worktrees",
    # Archive holds frozen historical artifacts; their refs are immutable
    # history and not load-bearing for current execution.
    ".dev/archive",
    # Inbox files are per-task artifacts written by past Track Agents from
    # inside their worktree (where `../PRD.md` was correct at creation
    # time). Don't rewrite history.
    ".dev/inbox",
    # Sackmann snapshot we'll pin in sprint 7 — opaque external data, no specs.
    "data/sources/sackmann_snapshot",
}

# File extensions to scan. Text files we know about. Anything else is skipped.
SCAN_EXTS = {".py", ".md", ".yaml", ".yml", ".json", ".sol", ".ts", ".tsx",
             ".js", ".jsx", ".sh", ".toml"}


def _should_skip_dir(path: Path, repo_root: Path) -> bool:
    rel = path.relative_to(repo_root).as_posix()
    if rel in SKIP_DIRS:
        return True
    for skip in SKIP_DIRS:
        if rel.startswith(skip + "/"):
            return True
    if path.name in SKIP_DIRS:
        return True
    return False


def _iter_files(repo_root: Path):
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in SCAN_EXTS:
            continue
        skip = False
        for parent in p.parents:
            if parent == repo_root:
                break
            if _should_skip_dir(parent, repo_root):
                skip = True
                break
        if skip:
            continue
        yield p


def _find_refs_in_file(text: str) -> list[tuple[int, str, str]]:
    """Return [(line_no, full_match, doc_name), ...] for each spec-doc ref.

    Captures the prefix character so we can tell whether the ref is already
    prefixed with ``docs/``, ``../``, etc.
    """
    out: list[tuple[int, str, str]] = []
    for doc in SPEC_DOCS:
        # Pattern: optional path prefix + doc name. Capture enough leading
        # chars so we can classify.
        # We match: (\S*) doc_name — i.e., any non-space chars immediately
        # before the doc name (so we capture "docs/", "../", "code/docs/", etc.)
        pattern = r"(\S*?)" + re.escape(doc)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for m in re.finditer(pattern, line):
                prefix = m.group(1)
                full = m.group(0)
                # Skip self-references inside the checker script itself
                # (this file contains the SPEC_DOCS literal).
                out.append((line_no, full, doc))
    return out


def _classify_ref(prefix: str) -> str:
    """Return one of: 'docs_prefix', 'parent_prefix', 'absolute', 'bare', 'other'."""
    if prefix.endswith("docs/"):
        return "docs_prefix"
    if prefix.endswith("../") or "../" in prefix:
        return "parent_prefix"
    if prefix.startswith("/") or (len(prefix) >= 2 and prefix[1] == ":"):
        return "absolute"
    if prefix == "" or prefix.endswith(("'", '"', "`", " ", ":", "/")):
        # bare or quoted — likely a citation
        if prefix == "":
            return "bare"
        return "bare_quoted"
    return "other"


def _check_path_resolves(prefix: str, doc: str, scanning_file: Path,
                         repo_root: Path) -> bool:
    """For load-bearing references (where the prefix forms a real path),
    check whether the resolved path actually exists."""
    cls = _classify_ref(prefix)
    if cls == "docs_prefix":
        # 'docs/PRD.md' resolves relative to repo root
        return (repo_root / "docs" / doc).is_file()
    if cls == "parent_prefix":
        # '../PRD.md' — resolve relative to the SCANNING file's dir
        target = (scanning_file.parent / (prefix + doc)).resolve()
        return target.is_file()
    if cls == "absolute":
        return Path(prefix + doc).is_file()
    return True  # bare / quoted citations aren't paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify spec-doc references resolve after the 2026-05-25 migration."
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(),
        help="Repo root (default: cwd). Pass code/ for the autopoiesis repo.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Also flag bare 'PRD.md' references that should probably be 'docs/PRD.md'.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print every ref found, not just failures.",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    if not repo_root.is_dir():
        print(f"ERROR: --repo-root {repo_root} is not a directory", file=sys.stderr)
        return 2

    # Sanity: confirm the canonical dir exists.
    canonical = repo_root / CANONICAL_DIR
    if not canonical.is_dir():
        print(f"WARN: canonical spec dir {canonical} does not exist — migration incomplete?",
              file=sys.stderr)

    # Confirm each spec doc is in the canonical dir.
    missing_canonical: list[str] = []
    for doc in SPEC_DOCS:
        if not (canonical / doc).is_file():
            missing_canonical.append(doc)
    if missing_canonical:
        print(f"ERROR: {missing_canonical} not found in {canonical}", file=sys.stderr)
        return 1

    broken_refs: list[tuple[Path, int, str, str]] = []
    suspicious_bare: list[tuple[Path, int, str, str]] = []
    total_refs = 0

    for fp in _iter_files(repo_root):
        # Skip the checker script itself (it contains literal SPEC_DOCS names).
        if fp.name == "check_spec_refs.py":
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        refs = _find_refs_in_file(text)
        for line_no, full, doc in refs:
            total_refs += 1
            # Extract the prefix (everything before the doc name in `full`)
            prefix = full[: -len(doc)]
            cls = _classify_ref(prefix)
            if args.verbose:
                rel = fp.relative_to(repo_root).as_posix()
                print(f"  {rel}:{line_no}  [{cls}]  {full}")
            # Load-bearing check
            if cls in ("docs_prefix", "parent_prefix", "absolute"):
                if not _check_path_resolves(prefix, doc, fp, repo_root):
                    broken_refs.append((fp, line_no, full, doc))
            # Strict mode: bare refs in non-archive code files
            if args.strict and cls in ("bare", "bare_quoted"):
                # Skip noisy text-only contexts: comments, docstrings, README
                # paragraphs. The checker can't easily tell so we just flag
                # files where bare refs may be load-bearing (Python source,
                # YAML config).
                if fp.suffix in (".py", ".yaml", ".yml"):
                    suspicious_bare.append((fp, line_no, full, doc))

    print(f"\nScanned {total_refs} references across the repo.")
    print(f"  Canonical dir: {canonical}")
    print(f"  Broken (load-bearing) refs: {len(broken_refs)}")
    print(f"  Suspicious bare refs (strict mode): {len(suspicious_bare)}")

    if broken_refs:
        print("\nBROKEN REFERENCES:")
        for fp, line_no, full, _doc in broken_refs:
            rel = fp.relative_to(repo_root).as_posix()
            print(f"  {rel}:{line_no}  {full}")
        return 1

    if args.strict and suspicious_bare:
        print("\nSUSPICIOUS BARE REFERENCES (may need 'docs/' prefix):")
        for fp, line_no, full, _doc in suspicious_bare:
            rel = fp.relative_to(repo_root).as_posix()
            print(f"  {rel}:{line_no}  {full}")
        # Strict mode: bare refs are warnings but not failures.

    print("\nOK — all load-bearing spec refs resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
