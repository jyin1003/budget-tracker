"""
ingestion/amex_ingest.py — Ingest Amex CSV statements into the budget DB.

File naming convention:  data/amex_<year>_<month>.csv
                         e.g. data/amex_2026_april.csv

CSV headers (tab-separated):
    Date | Date Processed | Description | Amount

Statement window:  26th of prior month → 25th of current month
Sign convention:   Amex exports charges as positive — we flip to negative
                   (expenses stored as negative, refunds/payments as positive)

Skip logic:
    For each CSV, derive the statement end date (25th of the file's month/year).
    Query the latest transaction date for the Amex account in the DB.
    If latest_date >= statement_end  →  entire file is already ingested, skip.

Atomicity:
    Each file is wrapped in a single BEGIN/COMMIT. Any error or interrupt
    rolls back the entire file — no partial ingestion is possible.

Usage:
    python -m ingestion.amex_ingest          # process all new CSVs
    python -m ingestion.amex_ingest --dry-run
"""

import csv
import sys
import argparse
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap — allow running as script or module from any cwd
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import DATA_DIR
from db.db import get_conn, init_db, get_or_create_account, resolve_merchant
from ingestion.merchant_cli import resolve_or_create_merchant

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACCOUNT_NAME = "Amex Platinum"
ACCOUNT_TYPE = "credit"
CURRENCY     = "AUD"

# Map month names (and abbreviations) → month numbers
_MONTH_MAP: dict[str, int] = {
    "january": 1,  "jan": 1,
    "february": 2, "feb": 2,
    "march": 3,    "mar": 3,
    "april": 4,    "apr": 4,
    "may": 5,
    "june": 6,     "jun": 6,
    "july": 7,     "jul": 7,
    "august": 8,   "aug": 8,
    "september": 9,"sep": 9,  "sept": 9,
    "october": 10, "oct": 10,
    "november": 11,"nov": 11,
    "december": 12,"dec": 12,
}

# ── ANSI helpers (match merchant_cli style) ──────────────────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

BOLD   = lambda t: _c("1", t)
DIM    = lambda t: _c("2", t)
CYAN   = lambda t: _c("96", t)
YELLOW = lambda t: _c("93", t)
GREEN  = lambda t: _c("92", t)
RED    = lambda t: _c("91", t)
BLUE   = lambda t: _c("94", t)


# ---------------------------------------------------------------------------
# File discovery & parsing
# ---------------------------------------------------------------------------

def find_amex_csvs() -> list[Path]:
    """Return all amex_*.csv files in DATA_DIR, sorted chronologically."""
    files = sorted(DATA_DIR.glob("amex_*.csv"))
    if not files:
        print(YELLOW(f"No amex_*.csv files found in {DATA_DIR}"))
    return files


def parse_filename(path: Path) -> tuple[int, int] | None:
    """
    Parse year and month from filename.
    Accepts:  amex_2026_april.csv  /  amex_2026_04.csv
    Returns:  (year, month_number) or None if unparseable.
    """
    stem = path.stem  # e.g. "amex_2026_april"
    parts = stem.split("_")
    if len(parts) != 3:
        return None

    _, year_str, month_str = parts

    try:
        year = int(year_str)
    except ValueError:
        return None

    # Numeric month
    if month_str.isdigit():
        month = int(month_str)
        if 1 <= month <= 12:
            return year, month
        return None

    # Named month
    month = _MONTH_MAP.get(month_str.lower())
    return (year, month) if month else None


def statement_window(year: int, month: int) -> tuple[date, date]:
    """
    Return (start, end) dates for the statement.
    End is always the 25th of (year, month).
    Start is the 26th of the prior month.
    """
    end = date(year, month, 25)
    if month == 1:
        start = date(year - 1, 12, 26)
    else:
        start = date(year, month - 1, 26)
    return start, end


def parse_amex_date(raw: str) -> str:
    """
    Parse Amex date strings → ISO 'YYYY-MM-DD'.
    Tries common formats: DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD.
    """
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: '{raw}'")


def read_csv(path: Path) -> list[dict]:
    """
    Read an Amex CSV. Handles tab or comma separators.
    Returns list of dicts with keys: date, description, amount (float, flipped).
    """
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        # Sniff delimiter
        sample = f.read(2048)
        f.seek(0)
        delimiter = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delimiter)

        for i, row in enumerate(reader, start=2):  # start=2 (header is row 1)
            try:
                raw_date   = row.get("Date", "").strip()
                raw_desc   = row.get("Description", "").strip()
                raw_amount = row.get("Amount", "").strip()

                if not raw_date or not raw_desc or not raw_amount:
                    continue  # skip blank/malformed rows

                iso_date = parse_amex_date(raw_date)
                # Amex: positive = charge, negative = payment/refund
                # DB convention: negative = expense, positive = income
                amount = -float(raw_amount)

                rows.append({
                    "date":        iso_date,
                    "description": raw_desc,
                    "amount":      round(amount, 2),
                })
            except (ValueError, KeyError) as e:
                print(YELLOW(f"  ⚠  Row {i} skipped — {e}"))

    return rows


# ---------------------------------------------------------------------------
# Skip detection
# ---------------------------------------------------------------------------

def latest_transaction_date(conn, account_id: int) -> date | None:
    """Return the most recent transaction date for this account, or None."""
    row = conn.execute(
        "SELECT MAX(date) as max_date FROM transactions WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    if row and row["max_date"]:
        return date.fromisoformat(row["max_date"])
    return None


def should_skip(latest: date | None, stmt_end: date) -> bool:
    """
    Skip the file if the DB already contains transactions up to or beyond
    the statement end date (25th of the file's month).
    """
    return latest is not None and latest >= stmt_end


# ---------------------------------------------------------------------------
# Core ingestion
# ---------------------------------------------------------------------------

def ingest_file(
    conn,
    path: Path,
    rows: list[dict],
    account_id: int,
    dry_run: bool = False,
) -> dict:
    """
    Ingest all rows from one CSV file inside a single atomic transaction.
    Returns a summary dict.
    """
    inserted = 0
    skipped  = 0
    errors   = 0

    print()
    print(BOLD(f"┌─ {path.name}") + f"  ({len(rows)} rows)")

    if dry_run:
        print(DIM("  [dry-run] No changes will be written."))

    try:
        conn.execute("BEGIN")

        for row in rows:
            raw_desc   = row["description"]
            amount     = row["amount"]
            tx_date    = row["date"]

            # ── Resolve merchant ──────────────────────────────────────────
            merchant_id = resolve_merchant(conn, raw_desc)
            category_id = None

            if merchant_id is None:
                # Look up category from known merchant alias
                pass  # handled below after interactive prompt

            if merchant_id is not None:
                # Already known — fetch its category
                cat_row = conn.execute(
                    "SELECT category_id FROM merchants WHERE id = ?", (merchant_id,)
                ).fetchone()
                category_id = cat_row["category_id"] if cat_row else None
            else:
                # Unknown merchant — interactive prompt
                if not dry_run:
                    merchant_id, category_id = resolve_or_create_merchant(conn, raw_desc)
                else:
                    print(DIM(f"  [dry-run] Unknown merchant: {raw_desc}"))

            # ── Insert transaction ────────────────────────────────────────
            if not dry_run:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO transactions
                        (date, amount, description, account_id, merchant_id, category_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (tx_date, amount, raw_desc, account_id, merchant_id, category_id),
                )
                if cur.rowcount:
                    inserted += 1
                    print(f"  {GREEN('✓')} {tx_date}  {raw_desc[:45]:<45}  {amount:>10.2f}")
                else:
                    skipped += 1
                    print(f"  {DIM('–')} {tx_date}  {DIM(raw_desc[:45]):<45}  {DIM(f'{amount:>10.2f}')}  {DIM('(duplicate)')}")
            else:
                inserted += 1  # count as "would insert" in dry-run

        if not dry_run:
            conn.execute("COMMIT")

    except Exception as e:
        conn.execute("ROLLBACK")
        print(RED(f"\n  ✗ Error — rolled back entire file: {e}"))
        errors = len(rows)
        inserted = 0
        skipped  = 0

    summary = {"inserted": inserted, "skipped": skipped, "errors": errors}
    status = GREEN("✓ Done") if not errors else RED("✗ Failed")
    print(BOLD(f"└─ {status}") + f"  inserted={inserted}  skipped={skipped}  errors={errors}")
    return summary


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Amex CSV statements.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and preview without writing to DB.")
    args = parser.parse_args()

    print(BOLD(BLUE("\n═══ Amex Statement Ingester ═══")))
    if args.dry_run:
        print(YELLOW("  DRY RUN — nothing will be written\n"))

    init_db()

    with get_conn() as conn:
        account_id = get_or_create_account(conn, ACCOUNT_NAME, ACCOUNT_TYPE, CURRENCY)
        latest = latest_transaction_date(conn, account_id)
        print(f"  Account : {ACCOUNT_NAME}  (id={account_id})")
        print(f"  Latest  : {latest or 'no transactions yet'}")

        csvs = find_amex_csvs()
        if not csvs:
            return

        print(f"\n  Found {len(csvs)} file(s) in {DATA_DIR}\n")

        total_inserted = 0
        total_skipped  = 0
        files_processed = 0
        files_skipped   = 0

        for path in csvs:
            parsed = parse_filename(path)
            if parsed is None:
                print(YELLOW(f"  ⚠  Skipping '{path.name}' — can't parse year/month from filename"))
                continue

            year, month = parsed
            stmt_start, stmt_end = statement_window(year, month)

            if should_skip(latest, stmt_end):
                print(f"  {DIM('–')} {path.name}  {DIM(f'(already ingested — latest={latest}, stmt_end={stmt_end})')}")
                files_skipped += 1
                continue

            print(f"\n  {CYAN(path.name)}  [{stmt_start} → {stmt_end}]")

            rows = read_csv(path)
            if not rows:
                print(YELLOW("    No valid rows found."))
                continue

            summary = ingest_file(conn, path, rows, account_id, dry_run=args.dry_run)
            total_inserted += summary["inserted"]
            total_skipped  += summary["skipped"]
            files_processed += 1

            # Update latest so subsequent files in the same run skip correctly
            if not args.dry_run and summary["inserted"] > 0:
                latest = latest_transaction_date(conn, account_id)

        # ── Final summary ─────────────────────────────────────────────────
        print()
        print(BOLD(BLUE("═══ Summary ═══")))
        print(f"  Files processed : {files_processed}")
        print(f"  Files skipped   : {files_skipped}")
        print(f"  Rows inserted   : {GREEN(str(total_inserted))}")
        print(f"  Rows skipped    : {DIM(str(total_skipped))}")
        print()


if __name__ == "__main__":
    main()