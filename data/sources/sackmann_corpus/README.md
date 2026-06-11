# Sackmann corpus — REAL vendored Jeff Sackmann data (2024–2026)

> **This directory holds the REAL Sackmann corpus.** The small SYNTHETIC,
> edge-case-bearing test fixtures live in the sibling
> [`../sackmann_snapshot/`](../sackmann_snapshot/README.md) directory. The two
> are kept deliberately separate so re-vendoring here never clobbers the
> hermetic test fixtures (and vice-versa).

Per docs/PRD.md §7 / §12, the canonical tennis match + ranking dataset is
[`JeffSackmann/tennis_atp`](https://github.com/JeffSackmann/tennis_atp) and
[`JeffSackmann/tennis_wta`](https://github.com/JeffSackmann/tennis_wta)
on GitHub (MIT licensed, free, 1968-present).

This is the **full real corpus** the flag-on real-signal path consumes
offline. The Sackmann tennis facets (ELO/ranking, surface advantage,
head-to-head, rest/recency) are computed against these CSVs. A
`SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)` reads them snapshot-first,
falling back to GitHub raw only on a miss.

## Files (real GitHub data)

| File | Contents (approx. rows, as vendored 2026-06-08) |
|---|---|
| `atp_matches_2024.csv` | ATP matches, 2024 season (~3076) |
| `atp_matches_2025.csv` | ATP matches, 2025 season (~2944) |
| `atp_matches_2026.csv` | ATP matches, 2026 season (~1322; dates 20260104..20260517) |
| `wta_matches_2024.csv` | WTA matches, 2024 season (~2689) |
| `wta_matches_2025.csv` | WTA matches, 2025 season (~2795) |
| `wta_matches_2026.csv` | WTA matches, 2026 season (~1168) |
| `atp_rankings_current.csv` | ATP rankings, recent dates (~34203) |
| `wta_rankings_current.csv` | WTA rankings, recent dates (~32060) |

## Schema

Match files follow Jeff Sackmann's canonical column layout (the same
`MATCH_COLUMNS` as the loader). Ranking files: `ranking_date, rank, player,
points`. Player IDs here are the REAL Sackmann namespace (distinct from the
`200000+` synthetic IDs used in `../sackmann_snapshot/`).

## Provenance / refresh policy

Vendored by [`scripts/vendor_sackmann_corpus.py`](../../../scripts/vendor_sackmann_corpus.py)
— a one-shot stdlib-`urllib` fetch of the current GitHub `master` for each
ATP/WTA file. It is **idempotent**: re-running overwrites the CSVs with the
latest upstream. Sackmann publishes the current year incrementally (~weeks
behind live), so refresh by re-running:

```bash
python scripts/vendor_sackmann_corpus.py
```

This is NOT the synthetic generator — to refresh the test fixtures instead,
see [`../sackmann_snapshot/README.md`](../sackmann_snapshot/README.md).
