# Personal Budget Tracker

## Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Ingest Amex statements

Downloaded CSVs are in `data/` following the naming convention:

```
data/amex_<year>_<month>.csv  # e.g. data/amex_2026_april.csv
```
CSV must have headers: `Date`, `Date Processed`, `Description`, `Amount`
Then run:

```bash
python -m ingestion.amex_ingest
```

### What it does

| Step | Detail |
|---|---|
| **Skip detection** | Derives the statement window (26th prev month → 25th current month). If the DB already has transactions up to the 25th, the file is skipped entirely. |
| **Atomic ingestion** | Each file is wrapped in a single transaction — interrupted runs roll back completely, leaving the DB clean. |
| **Merchant resolution** | Known merchants (via `merchant_aliases`) are auto-categorised. New ones trigger an interactive CLI prompt. |
| **Sign convention** | Amex charges (positive in CSV) are stored as negative in the DB. Refunds/payments stay positive. |

### Options

```bash
python -m ingestion.amex_ingest --dry-run   # parse & preview, no DB writes
```

### During ingestion — new merchants

When an unknown merchant is encountered you'll be prompted to:

1. Confirm or edit the merchant name (default: title-cased raw description)
2. Pick an existing category **or** type `N` to create a new one

The mapping is saved permanently — the same raw description will be auto-resolved in all future runs.

## Project structure

```
budget-tracker/
├── config.py                  # paths (BASE_DIR, DATA_DIR, DB_PATH)
├── db/
│   ├── db.py                  # connection helper + convenience functions
│   └── schema.sql             # table definitions + category seeds
├── ingestion/
│   ├── amex_ingest.py         # main ingester
│   └── merchant_cli.py        # interactive merchant categorisation CLI
├── data/                      # gitignored — CSVs + budget.db live here
└── requirements.txt
```