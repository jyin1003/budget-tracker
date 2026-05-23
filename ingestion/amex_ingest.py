"""
ingestion/amex_ingest.py — Parse and stage Amex CSV statements.

File naming convention:  data/amex_<year>_<month>.csv
                         e.g. data/amex_2026_april.csv

CSV headers (tab or comma separated):
    Date | Date Processed | Description | Amount

Statement window:  26th of prior month → 25th of current month
Sign convention:   Amex exports charges as positive — stored as negative
                   (expenses = negative, refunds/payments = positive)

Skip logic:
    For each CSV, derive the statement end date (25th of the file's month).
    Query the latest transaction date for the Amex account in the DB.
    If latest_date >= statement_end → file is fully ingested, skip.

Public API:
    ACCOUNT_NAME  : str
    parse(conn)   -> list[StagedFile]
    commit(conn, staged_files, resolved)
"""

import csv
import sys
from datetime import date, datetime
from pathlib import Path
from dataclasses import dataclass, field

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import DATA_DIR
from db.db import get_or_create_account, resolve_merchant

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACCOUNT_NAME = "Amex Platinum"
ACCOUNT_TYPE = "credit"
CURRENCY     = "AUD"

_MONTH_MAP: dict[str, int] = {
    "january": 1,  "jan": 1,
    "february": 2, "feb": 2,
    "march": 3,    "mar": 3,
    "april": 4,    "apr": 4,
    "may": 5,
    "june": 6,     "jun": 6,
    "july": 7,     "jul": 7,
    "august": 8,   "aug": 8,
    "september": 9,"sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11,"nov": 11,
    "december": 12,"dec": 12,
}

# ── ANSI helpers ─────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

BOLD   = lambda t: _c("1", t)
DIM    = lambda t: _c("2", t)
CYAN   = lambda t: _c("96", t)
YELLOW = lambda t: _c("93", t)
GREEN  = lambda t: _c("92", t)
RED    = lambda t: _c("91", t)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ParsedRow:
    date:        str
    amount:      float
    description: str


@dataclass
class StagedFile:
    path:       Path
    account_id: int
    stmt_start: date
    stmt_end:   date
    rows:       list[ParsedRow] = field(default_factory=list)
    unknowns:   list[str]       = field(default_factory=list)  # raw descriptions needing CLI


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _find_csvs() -> list[Path]:
    return sorted(DATA_DIR.glob("amex_*.csv"))


def _parse_filename(path: Path) -> tuple[int, int] | None:
    parts = path.stem.split("_")
    if len(parts) != 3:
        return None
    _, year_str, month_str = parts
    try:
        year = int(year_str)
    except ValueError:
        return None
    if month_str.isdigit():
        month = int(month_str)
        return (year, month) if 1 <= month <= 12 else None
    month = _MONTH_MAP.get(month_str.lower())
    return (year, month) if month else None


def _statement_window(year: int, month: int) -> tuple[date, date]:
    end = date(year, month, 25)
    start = date(year - 1, 12, 26) if month == 1 else date(year, month - 1, 26)
    return start, end


def _latest_date(conn, account_id: int) -> date | None:
    row = conn.execute(
        "SELECT MAX(date) as d FROM transactions WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return date.fromisoformat(row["d"]) if row and row["d"] else None


def _parse_date(raw: str) -> str:
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: '{raw}'")


def _read_csv(path: Path) -> list[ParsedRow]:
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        for i, row in enumerate(reader, start=2):
            try:
                raw_date   = row.get("Date", "").strip()
                raw_desc   = row.get("Description", "").strip()
                raw_amount = row.get("Amount", "").strip()
                if not raw_date or not raw_desc or not raw_amount:
                    continue
                rows.append(ParsedRow(
                    date        = _parse_date(raw_date),
                    amount      = round(-float(raw_amount), 2),  # flip sign
                    description = raw_desc,
                ))
            except (ValueError, KeyError) as e:
                print(YELLOW(f"  ⚠  {path.name} row {i} skipped — {e}"))
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(conn) -> list[StagedFile]:
    """
    Discover all amex_*.csv files, skip already-ingested ones, parse the rest.
    Returns a list of StagedFile — one per CSV to be committed.
    Also populates StagedFile.unknowns with raw descriptions that have no alias.
    """
    csvs = _find_csvs()
    if not csvs:
        print(YELLOW(f"  [amex] No amex_*.csv files found in {DATA_DIR}"))
        return []

    account_id = get_or_create_account(conn, ACCOUNT_NAME, ACCOUNT_TYPE, CURRENCY)
    latest     = _latest_date(conn, account_id)

    print(f"  {CYAN('[amex]')} account_id={account_id}  latest_tx={latest or 'none'}")

    staged: list[StagedFile] = []

    for path in csvs:
        parsed = _parse_filename(path)
        if parsed is None:
            print(YELLOW(f"  [amex] Skipping '{path.name}' — can't parse year/month"))
            continue

        year, month = parsed
        stmt_start, stmt_end = _statement_window(year, month)

        if latest is not None and latest >= stmt_end:
            print(f"  {DIM('[amex]')} {path.name}  {DIM(f'already ingested (latest={latest} >= {stmt_end})')}")
            continue

        print(f"  {CYAN('[amex]')} {path.name}  [{stmt_start} → {stmt_end}]  parsing...")
        rows = _read_csv(path)
        if not rows:
            print(YELLOW(f"  [amex] {path.name} — no valid rows, skipping"))
            continue

        # Find which descriptions have no existing alias
        seen: set[str] = set()
        unknowns: list[str] = []
        for row in rows:
            desc = row.description
            if desc not in seen:
                seen.add(desc)
                if resolve_merchant(conn, desc) is None:
                    unknowns.append(desc)

        staged.append(StagedFile(
            path       = path,
            account_id = account_id,
            stmt_start = stmt_start,
            stmt_end   = stmt_end,
            rows       = rows,
            unknowns   = unknowns,
        ))

    return staged


def commit(
    conn,
    staged_files: list[StagedFile],
    resolved: dict[str, tuple[int, int]],
    dry_run: bool = False,
) -> dict:
    """
    Insert all staged rows atomically per file.
    resolved: mapping from merchant_cli — raw_description -> (merchant_id, category_id)
    Returns aggregate summary dict.
    """
    total_inserted = 0
    total_skipped  = 0
    total_errors   = 0

    for sf in staged_files:
        inserted = 0
        print()
        print(BOLD(f"┌─ {sf.path.name}") + f"  ({len(sf.rows)} rows)")

        if dry_run:
            print(DIM("  [dry-run] No changes will be written."))
            total_inserted += len(sf.rows)
            print(BOLD(f"└─ {GREEN('✓ dry-run')}  would insert={len(sf.rows)}"))
            continue

        try:
            conn.execute("BEGIN")

            for row in sf.rows:
                # Resolve merchant: existing alias → resolved map → None
                merchant_id = resolve_merchant(conn, row.description)
                category_id = None

                if merchant_id is not None:
                    cat = conn.execute(
                        "SELECT category_id FROM merchants WHERE id = ?", (merchant_id,)
                    ).fetchone()
                    category_id = cat["category_id"] if cat else None
                elif row.description in resolved:
                    merchant_id, category_id = resolved[row.description]

                cur = conn.execute(
                    """
                    INSERT INTO transactions
                        (date, amount, description, account_id, merchant_id, category_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (row.date, row.amount, row.description,
                     sf.account_id, merchant_id, category_id),
                )
                inserted += 1
                print(f"  {GREEN('✓')} {row.date}  {row.description[:45]:<45}  {row.amount:>10.2f}")

            conn.execute("COMMIT")
            total_inserted += inserted
            print(BOLD(f"└─ {GREEN('✓ Done')}  inserted={inserted}"))

        except Exception as e:
            conn.execute("ROLLBACK")
            print(RED(f"\n  ✗ Error — rolled back: {e}"))
            total_errors += len(sf.rows)

    return {"inserted": total_inserted, "skipped": 0, "errors": total_errors}