# Personal Budget Tracker

## Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run
Run dashboard with: `python dashboard/server.py`
Run DB viewer with: `python db_viewer.py`

## Project structure

```
budget-tracker/
├── config.py
├── db/
│   ├── db.py                  # connection helper + convenience functions
│   ├── budget.db              # DB
│   └── schema.sql             # table definitions + category seeds
├── ingestion/
│   ├── ingest.py              # main ingester
│   ├── <merchant>_ingest.py   # merchant ingesters
│   ├── merchant_match.py      # utilities for merchant_cli
│   └── merchant_cli.py        # interactive merchant categorisation CLI
├── data/                      # gitignored — CSVs live here
└── requirements.txt
```

## DB Viewer
Lightweight browser viewer of the `budget.db`.
- Transactions tab — paginated table, filterable by description/merchant
- Merchants tab — all merchants with their category
- Categories tab — category list with transaction counts

Run with: `python db_viewer.py`
Open at: [link](http://localhost:8765)

## Ingestion
Run with: `python ingestion/ingest.py`

Phase 1 — Parse
`<merchant>_ingest.parse()`  →  reads CSVs, skips ingested ones, returns StagedFile list
(future ingesters do the same)

Phase 2 — CLI (once, for everything)
  all unknowns deduplicated across every ingester
  `merchant_cli.run_merchant_cli()`  →  returns resolved dict {raw → (merchant_id, cat_id)}

Phase 3 — Commit
  `<merchant>_ingest.commit()`  →  BEGIN → insert rows using resolved map → COMMIT
  (future ingesters same)

Phase 4 — Rule engine override
  `rules.py`  →  overrides certain transaction properties based on hardcoded rules

### What it does

| Step | Detail |
|---|---|
| **Atomic ingestion** | Each file is wrapped in a single transaction — interrupted runs roll back completely, leaving the DB clean. |
| **Merchant resolution** | Known merchants (via `merchant_aliases`) are auto-categorised. New ones trigger an interactive CLI prompt. |

### During ingestion — new merchants

When an unknown merchant is encountered you'll be prompted to:

1. Confirm or edit the merchant name (default: title-cased raw description)
2. Pick an existing category **or** type `N` to create a new one

The mapping is saved permanently — the same raw description will be auto-resolved in all future runs.

## To Do
- Charts use fetch_spending() under the hood, so they'll inherit the Entertainment double-counting bug until that's resolved — worth fixing that first if you're about to start relying on these trend lines.- whats this
- income not showing (its postive)