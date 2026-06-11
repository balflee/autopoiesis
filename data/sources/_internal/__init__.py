"""Track E internal helpers — generator scripts for vendored fixtures.

Modules here are *generators*, not runtime code. Production reads use the
fixtures they produce (e.g. ``data/sources/sackmann_snapshot/``); the
generators live in-tree so reviewers can re-derive the fixtures
bit-for-bit.
"""
