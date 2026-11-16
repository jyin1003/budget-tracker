"""
ingestion/nab_ingest.py — Parse and stage NAB CSV exports.

File naming convention:  data/nab_*.csv   (anything after "nab_" is fine,
                         e.g. data/nab_2026_november.csv)

Columns used (header row required, others ignored):
    Date | Amount | Transaction Details | Category | Merchant Name

Sign convention: NAB already exports debits as negative and credits as
positive, which matches the DB convention — stored as-is.

Description strategy: Merchant Name is the alias key when present (it is much
cleaner than Transaction Details and avoids a new alias per store/terminal).
Falls back to Transaction Details. When the two differ, the full Transaction
Details string is kept in transactions.notes so nothing is lost.

Category hints: NAB's own "Category" is mapped via nab_categories.toml.
    [hints]     -> suggested category for merchants with no alias yet; passed to
                   merchant_cli as a pre-selection (never overrides anything).
    [overrides] -> applied per transaction on positive amounts (e.g. Refund ->
                   Income), regardless of the merchant's own category.

Skip / deduplication strategy:
    NAB CSVs have no stable transaction ID, so each row gets a deterministic
    source_id = "nab:" + sha1(account | date | amount | details | n), where n is
    the occurrence index of identical (date, amount, details) rows within the
    file. Re-ingesting the same file, or overlapping exports, is therefore safe:
    rows already in the DB are filtered out in parse() and INSERT OR IGNORE
    catches anything else on the unique source_id index.

Public API:
    ACCOUNT_NAME : str
    parse(conn)  -> list[StagedFile]
    commit(conn, staged_files, resolved, dry_run=False)
"""

import csv
import hashlib
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import DATA_DIR
from db.db import get_or_create_account, resolve_merchant

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACCOUNT_NAME = "NAB Classic"   # TODO: confirm account name
ACCOUNT_TYPE = "debit"
CURRENCY     = "AUD"

MAP_PATH = _ROOT / "ingestion" / "nab_categories.toml"

_DATE_FORMATS = ("%d %b %y", "%d %b %Y", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d")

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
    date:        str          # ISO 'YYYY-MM-DD'
    amount:      float        # negative = expense, positive = income
    description: str          # alias key (Merchant Name, else Transaction Details)
    source_id:   str          # "nab:<hash>"
    notes:       str | None = None   # full Transaction Details when it differs
    nab_category:      str | None = None   # NAB's raw Category string
    category_override: str | None = None   # forced category name (from [overrides])


@dataclass
class StagedFile:
    path:       Path
    account_id: int
    rows:       list[ParsedRow] = field(default_factory=list)   # new rows only
    unknowns:   list[str]       = field(default_factory=list)
    hints:      dict[str, str]  = field(default_factory=dict)   # raw description -> suggested category name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_category_map() -> tuple[dict[str, str], dict[str, str]]:
    """Return (hints, overrides), keys lower-cased. Empty if the file is missing."""
    if not MAP_PATH.exists():
        print(YELLOW(f"  [nab] {MAP_PATH.name} not found — no category hints"))
        return {}, {}
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with MAP_PATH.open("rb") as f:
        raw = tomllib.load(f)
    norm = lambda d: {k.strip().lower(): v for k, v in d.items()}
    return norm(raw.get("hints", {})), norm(raw.get("overrides", {}))


def _parse_date(raw: str) -> str:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: '{raw}'")


def _parse_amount(raw: str) -> float:
    return round(float(raw.replace("$", "").replace(",", "").strip()), 2)


def _source_id(date: str, amount: float, details: str, n: int) -> str:
    key = f"{ACCOUNT_NAME}|{date}|{amount:.2f}|{details}|{n}"
    return "nab:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def _already_ingested(conn, source_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM transactions WHERE source_id = ?", (source_id,)
    ).fetchone() is not None


def _read_csv(path: Path, overrides: dict[str, str]) -> list[ParsedRow]:
    rows: list[ParsedRow] = []
    seen: Counter = Counter()   # (date, amount, details) -> occurrences so far

    with path.open(newline="", encoding="utf-8-sig") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        # Tolerate stray whitespace in header names
        reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]

        for i, row in enumerate(reader, start=2):
            try:
                raw_date    = (row.get("Date") or "").strip()
                raw_amount  = (row.get("Amount") or "").strip()
                details     = (row.get("Transaction Details") or "").strip()
                merchant    = (row.get("Merchant Name") or "").strip()
                nab_cat     = (row.get("Category") or "").strip()
                if not raw_date or not raw_amount or not (details or merchant):
                    continue

                date   = _parse_date(raw_date)
                amount = _parse_amount(raw_amount)
                base   = details or merchant

                key = (date, amount, base)
                n = seen[key]
                seen[key] += 1

                desc = merchant or details
                override = overrides.get(nab_cat.lower()) if amount > 0 else None
                rows.append(ParsedRow(
                    date        = date,
                    amount      = amount,
                    description = desc,
                    source_id   = _source_id(date, amount, base, n),
                    notes       = details if (merchant and details and details != merchant) else None,
                    nab_category      = nab_cat or None,
                    category_override = override,
                ))
            except (ValueError, KeyError) as e:
                print(YELLOW(f"  ⚠  {path.name} row {i} skipped — {e}"))
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(conn) -> list[StagedFile]:
    """
    Discover nab_*.csv files, drop rows already in the DB, and flag
    descriptions with no alias. One StagedFile per CSV that has new rows.
    """
    csvs = sorted(DATA_DIR.glob("nab_*.csv"))
    if not csvs:
        print(YELLOW(f"  [nab] No nab_*.csv files found in {DATA_DIR}"))
        return []

    account_id = get_or_create_account(conn, ACCOUNT_NAME, ACCOUNT_TYPE, CURRENCY)
    print(f"  {CYAN('[nab]')} account_id={account_id}")
    hint_map, overrides = _load_category_map()

    staged: list[StagedFile] = []

    for path in csvs:
        all_rows = _read_csv(path, overrides)
        if not all_rows:
            print(YELLOW(f"  [nab] {path.name} — no valid rows, skipping"))
            continue

        new_rows = [r for r in all_rows if not _already_ingested(conn, r.source_id)]
        print(
            f"  {CYAN('[nab]')} {path.name}  {len(all_rows)} rows  "
            f"({len(new_rows)} new, {len(all_rows) - len(new_rows)} already ingested)"
        )
        if not new_rows:
            print(f"  {DIM('[nab]')} {path.name}  {DIM('all already ingested — skipping')}")
            continue

        seen: set[str] = set()
        unknowns: list[str] = []
        hints: dict[str, str] = {}
        for row in new_rows:
            if row.description not in seen:
                seen.add(row.description)
                if resolve_merchant(conn, row.description) is None:
                    unknowns.append(row.description)
            # Merchant-level hint: skip rows that get a per-transaction override
            # (a refund must not make its merchant "Income").
            if (row.description not in hints and not row.category_override
                    and row.nab_category):
                suggested = hint_map.get(row.nab_category.lower())
                if suggested:
                    hints[row.description] = suggested

        staged.append(StagedFile(
            path       = path,
            account_id = account_id,
            rows       = new_rows,
            unknowns   = unknowns,
            hints      = hints,
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
    resolved: raw_description -> (merchant_id, category_id) from merchant_cli.
    Returns aggregate summary dict.
    """
    total_inserted = 0
    total_skipped  = 0
    total_errors   = 0

    for sf in staged_files:
        print()
        print(BOLD(f"┌─ {sf.path.name}") + f"  ({len(sf.rows)} new rows)")

        if dry_run:
            print(DIM("  [dry-run] No changes will be written."))
            total_inserted += len(sf.rows)
            print(BOLD(f"└─ {GREEN('✓ dry-run')}  would insert={len(sf.rows)}"))
            continue

        inserted = 0
        skipped  = 0
        try:
            conn.execute("BEGIN")

            for row in sf.rows:
                merchant_id = resolve_merchant(conn, row.description)
                category_id = None

                if merchant_id is not None:
                    cat = conn.execute(
                        "SELECT category_id FROM merchants WHERE id = ?", (merchant_id,)
                    ).fetchone()
                    category_id = cat["category_id"] if cat else None
                elif row.description in resolved:
                    merchant_id, category_id = resolved[row.description]

                if row.category_override:
                    ov = conn.execute(
                        "SELECT id FROM categories WHERE LOWER(name) = LOWER(?)",
                        (row.category_override,),
                    ).fetchone()
                    if ov:
                        category_id = ov["id"]

                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO transactions
                        (date, amount, description, account_id,
                         merchant_id, category_id, notes, source_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (row.date, row.amount, row.description, sf.account_id,
                     merchant_id, category_id, row.notes, row.source_id),
                )
                if cur.rowcount:
                    inserted += 1
                    print(f"  {GREEN('✓')} {row.date}  {row.description[:45]:<45}  {row.amount:>10.2f}")
                else:
                    skipped += 1
                    print(f"  {DIM('·')} {row.date}  {DIM(row.description[:45]):<54}  {DIM('already ingested')}")

            conn.execute("COMMIT")
            total_inserted += inserted
            total_skipped  += skipped
            print(BOLD(f"└─ {GREEN('✓ Done')}  inserted={inserted}  skipped={skipped}"))

        except Exception as e:
            conn.execute("ROLLBACK")
            print(RED(f"\n  ✗ Error — rolled back: {e}"))
            total_errors += len(sf.rows)

    return {"inserted": total_inserted, "skipped": total_skipped, "errors": total_errors}