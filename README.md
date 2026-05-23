# Personal Budget Tracker

## Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
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

## Ingestion
Phase 1 — Parse
`<merchant>_ingest.parse()`  →  reads CSVs, skips ingested ones, returns StagedFile list
(future ingesters do the same)

Phase 2 — CLI (once, for everything)
  all unknowns deduplicated across every ingester
  `merchant_cli.run_merchant_cli()`  →  returns resolved dict {raw → (merchant_id, cat_id)}

Phase 3 — Commit
  `<merchant>_ingest.commit()`  →  BEGIN → insert rows using resolved map → COMMIT
  (future ingesters same)

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
