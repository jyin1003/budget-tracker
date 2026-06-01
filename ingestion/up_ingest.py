"""
ingestion/up_ingest.py — Fetch and stage Up Bank transactions via the Up API.

Fetches SETTLED transactions from all TRANSACTIONAL accounts only (savers excluded).
Reads UP_TOKEN from .env in the project root.

Skip / deduplication strategy:
    On each run, fetch the last 7 days of transactions (or everything on first run).
    The 7-day window safely covers any late-settling transactions. Rows already in
    the DB are silently skipped via INSERT OR IGNORE on the unique source_id column,
    which stores the Up transaction UUID (prefixed "up:").

Sign convention: Up returns negative amounts for expenses, positive for income —
stored as-is (matches DB convention).

Description strategy: rawText (messy bank string) is used as the alias key when
available, falling back to description. This keeps alias matching consistent with
how CSV-based ingesters work.

Transfers between Up accounts are skipped to avoid double-counting.

Public API:
    ACCOUNT_NAME_PREFIX  : str
    parse(conn)          -> list[StagedFile]
    commit(conn, staged_files, resolved)
"""

import json
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent if _HERE.name == "ingestion" else _HERE
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db.db import get_or_create_account, resolve_merchant

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL            = "https://api.up.com.au/api/v1"
ACCOUNT_NAME_PREFIX = "Up"    # accounts stored as e.g. "Up Spending"
ACCOUNT_TYPE        = "debit"
CURRENCY            = "AUD"
PAGE_SIZE           = 100     # max allowed by Up API
LOOKBACK_DAYS       = 7       # re-fetch window; source_id deduplicates overlaps

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
# .env loader (stdlib only — no python-dotenv)
# ---------------------------------------------------------------------------

def _load_token() -> str:
    """Read UP_TOKEN from .env file in project root. Raises if not found."""
    env_path = _ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == "UP_TOKEN":
                return val.strip().strip('"').strip("'")
    raise RuntimeError(
        "UP_TOKEN not found in .env — add a line: UP_TOKEN=up:yeah:your_token_here"
    )


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def _api_get(token: str, url: str) -> dict:
    """Make an authenticated GET request to the Up API. Returns parsed JSON."""
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Up API {e.code} at {url}: {body}") from e


def _fetch_transactional_accounts(token: str) -> list[dict]:
    """Return all TRANSACTIONAL accounts for the authenticated user."""
    url = (
        f"{BASE_URL}/accounts"
        f"?filter[accountType]=TRANSACTIONAL"
        f"&page[size]={PAGE_SIZE}"
    )
    accounts = []
    while url:
        data = _api_get(token, url)
        accounts.extend(data.get("data", []))
        url = data.get("links", {}).get("next")
    return accounts


def _fetch_transactions(token: str, account_id: str, since_iso: str) -> list[dict]:
    """
    Fetch all SETTLED transactions for a given Up account ID since since_iso.
    Follows cursor pagination until exhausted.
    """
    params = (
        f"filter[status]=SETTLED"
        f"&filter[since]={urllib.parse.quote(since_iso)}"
        f"&page[size]={PAGE_SIZE}"
    )
    url = f"{BASE_URL}/accounts/{account_id}/transactions?{params}"
    transactions = []
    while url:
        data = _api_get(token, url)
        transactions.extend(data.get("data", []))
        url = data.get("links", {}).get("next")
    return transactions


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ParsedRow:
    date:        str    # ISO 8601: 'YYYY-MM-DD'
    amount:      float  # negative = expense, positive = income
    description: str    # raw alias key (rawText if available, else description)
    source_id:   str    # Up transaction UUID, prefixed "up:"


@dataclass
class StagedFile:
    """One StagedFile per Up account (analogous to one CSV in amex_ingest)."""
    up_account_id:   str
    up_account_name: str             # Up's display name e.g. "Spending"
    account_id:      int             # DB accounts.id
    since_date:      date            # fetch window start (for display only)
    rows:            list[ParsedRow] = field(default_factory=list)
    unknowns:        list[str]       = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_date(conn, account_id: int) -> date | None:
    row = conn.execute(
        "SELECT MAX(date) as d FROM transactions WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return date.fromisoformat(row["d"]) if row and row["d"] else None


def _settled_date(tx: dict) -> str:
    """Return YYYY-MM-DD from settledAt (falls back to createdAt)."""
    raw = tx["attributes"].get("settledAt") or tx["attributes"]["createdAt"]
    return datetime.fromisoformat(raw).date().isoformat()


def _raw_description(tx: dict) -> str:
    """Use rawText as alias key when available; fall back to description."""
    attrs = tx["attributes"]
    return (attrs.get("rawText") or attrs["description"]).strip()


def _is_transfer(tx: dict) -> bool:
    """True if this transaction is a transfer between accounts."""
    return tx["relationships"].get("transferAccount", {}).get("data") is not None


def _already_ingested(conn, source_id: str) -> bool:
    """Check whether a transaction with this source_id is already in the DB."""
    row = conn.execute(
        "SELECT 1 FROM transactions WHERE source_id = ?", (source_id,)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(conn) -> list[StagedFile]:
    """
    Authenticate with the Up API, fetch TRANSACTIONAL accounts, and pull
    SETTLED non-transfer transactions within the fetch window.

    Fetch window:
      - First run (no rows in DB for this account): fetch everything.
      - Subsequent runs: fetch the last LOOKBACK_DAYS days.
        Transactions already in the DB are identified by source_id and excluded
        from unknowns / commit — INSERT OR IGNORE handles the DB side.

    Returns a list of StagedFile — one per Up account that has rows to process.
    Also populates StagedFile.unknowns with descriptions that need CLI resolution.
    """
    try:
        token = _load_token()
    except RuntimeError as e:
        print(RED(f"  [up] {e}"))
        return []

    try:
        _api_get(token, f"{BASE_URL}/util/ping")
    except RuntimeError as e:
        print(RED(f"  [up] Auth failed — {e}"))
        return []

    try:
        up_accounts = _fetch_transactional_accounts(token)
    except RuntimeError as e:
        print(RED(f"  [up] Failed to fetch accounts — {e}"))
        return []

    if not up_accounts:
        print(YELLOW("  [up] No TRANSACTIONAL accounts found"))
        return []

    staged: list[StagedFile] = []

    for up_acc in up_accounts:
        up_acc_id = up_acc["id"]
        display   = up_acc["attributes"]["displayName"]
        db_name   = f"{ACCOUNT_NAME_PREFIX} {display}"   # e.g. "Up Spending"

        db_acc_id = get_or_create_account(conn, db_name, ACCOUNT_TYPE, CURRENCY)
        latest    = _latest_date(conn, db_acc_id)

        # Determine fetch window start
        if latest is None:
            # First run — fetch all history; Up requires a since param so use a
            # far-past date.
            since_date = date(2026, 1, 1)
        else:
            since_date = latest - timedelta(days=LOOKBACK_DAYS)

        since_iso = f"{since_date.isoformat()}T00:00:00+00:00"

        print(
            f"  {CYAN('[up]')} {db_name}  account_id={db_acc_id}"
            f"  latest_tx={latest or 'none'}"
            f"  fetching since={since_date}"
        )

        try:
            raw_txs = _fetch_transactions(token, up_acc_id, since_iso)
        except RuntimeError as e:
            print(RED(f"  [up] Failed to fetch transactions for {db_name} — {e}"))
            continue

        # Drop transfers
        raw_txs = [t for t in raw_txs if not _is_transfer(t)]

        if not raw_txs:
            print(f"  {DIM('[up]')} {db_name}  {DIM('no transactions in window')}")
            continue

        # Build rows, skipping source_ids already in DB for unknowns tracking
        rows:     list[ParsedRow] = []
        seen:     set[str]        = set()   # deduplicate description -> unknowns
        unknowns: list[str]       = []

        for tx in raw_txs:
            source_id   = f"up:{tx['id']}"
            amount      = round(float(tx["attributes"]["amount"]["value"]), 2)
            desc        = _raw_description(tx)
            tx_date     = _settled_date(tx)

            rows.append(ParsedRow(
                date        = tx_date,
                amount      = amount,
                description = desc,
                source_id   = source_id,
            ))

            # Only prompt for unknowns on rows not already in DB
            if not _already_ingested(conn, source_id):
                if desc not in seen:
                    seen.add(desc)
                    if resolve_merchant(conn, desc) is None:
                        unknowns.append(desc)

        new_count = sum(1 for r in rows if not _already_ingested(conn, r.source_id))
        print(
            f"  {CYAN('[up]')} {db_name}"
            f"  {len(rows)} in window  ({new_count} new, {len(rows)-new_count} already ingested)"
        )

        if new_count == 0:
            print(f"  {DIM('[up]')} {db_name}  {DIM('all already ingested — skipping')}")
            continue

        staged.append(StagedFile(
            up_account_id   = up_acc_id,
            up_account_name = display,
            account_id      = db_acc_id,
            since_date      = since_date,
            rows            = rows,
            unknowns        = unknowns,
        ))

    return staged


def commit(
    conn,
    staged_files: list[StagedFile],
    resolved: dict[str, tuple[int, int]],
    dry_run: bool = False,
) -> dict:
    """
    Insert all staged rows atomically per Up account.
    Rows already in the DB are silently skipped via INSERT OR IGNORE on source_id.
    resolved: mapping from merchant_cli — raw_description -> (merchant_id, category_id)
    Returns aggregate summary dict.
    """
    total_inserted = 0
    total_skipped  = 0
    total_errors   = 0

    for sf in staged_files:
        label = f"Up / {sf.up_account_name}"
        print()
        print(BOLD(f"┌─ {label}") + f"  ({len(sf.rows)} rows in window)")

        if dry_run:
            print(DIM("  [dry-run] No changes will be written."))
            new = sum(1 for r in sf.rows if not _already_ingested(conn, r.source_id))
            print(BOLD(f"└─ {GREEN('✓ dry-run')}  would insert≈{new}"))
            total_inserted += new
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

                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO transactions
                        (date, amount, description, account_id,
                         merchant_id, category_id, source_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.date, row.amount, row.description, sf.account_id,
                        merchant_id, category_id, row.source_id,
                    ),
                )
                if cur.rowcount:
                    inserted += 1
                    print(
                        f"  {GREEN('✓')} {row.date}"
                        f"  {row.description[:45]:<45}"
                        f"  {row.amount:>10.2f}"
                    )
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