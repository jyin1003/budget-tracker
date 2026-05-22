"""
db/db.py — SQLite connection helper for the budget tracker.

Usage:
    from db.db import get_conn, init_db

    init_db()                  # run once at startup; safe to call repeatedly
    with get_conn() as conn:
        conn.execute(...)
"""

import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR, SCHEMA, DB_PATH, DB_DIR

def get_conn() -> sqlite3.Connection:
    """
    Return a connection to the SQLite database.
    - Row factory set so rows behave like dicts: row['column_name']
    - Foreign key enforcement turned on per connection
    Use as a context manager for automatic commit/rollback:
        with get_conn() as conn:
            conn.execute(...)
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """
    Create all tables (if they don't exist) by running schema.sql.
    Safe to call on every startup — uses CREATE IF NOT EXISTS throughout.
    """
    schema = SCHEMA.read_text()
    with get_conn() as conn:
        conn.executescript(schema)
    print(f"[db] Initialised database at {DB_PATH}")


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def get_or_create_account(
    conn: sqlite3.Connection,
    name: str,
    account_type: str,
    currency: str = "AUD",
) -> int:
    """Return the id of an account, creating it if it doesn't exist."""
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO accounts (name, type, currency) VALUES (?, ?, ?)",
        (name, account_type, currency),
    )
    conn.commit()
    return cur.lastrowid


def resolve_merchant(conn: sqlite3.Connection, raw_description: str) -> int | None:
    """
    Look up a raw description string in merchant_aliases.
    Returns merchant_id if found, None if this description is unknown.
    """
    row = conn.execute(
        "SELECT merchant_id FROM merchant_aliases WHERE raw_description = ?",
        (raw_description,),
    ).fetchone()
    return row["merchant_id"] if row else None


def save_merchant_alias(
    conn: sqlite3.Connection,
    raw_description: str,
    merchant_name: str,
    category_id: int,
) -> int:
    """
    Persist a new merchant (if not already known) and alias the raw description to it.
    Returns the merchant_id.
    """
    row = conn.execute(
        "SELECT id FROM merchants WHERE name = ?", (merchant_name,)
    ).fetchone()
    if row:
        merchant_id = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO merchants (name, category_id) VALUES (?, ?)",
            (merchant_name, category_id),
        )
        merchant_id = cur.lastrowid

    conn.execute(
        "INSERT OR IGNORE INTO merchant_aliases (raw_description, merchant_id) VALUES (?, ?)",
        (raw_description, merchant_id),
    )
    conn.commit()
    return merchant_id


def insert_transaction(
    conn: sqlite3.Connection,
    date: str,
    amount: float,
    description: str,
    account_id: int,
    merchant_id: int | None = None,
    category_id: int | None = None,
    notes: str | None = None,
) -> int | None:
    """
    Insert a transaction. Returns the new row id, or None if it was a duplicate
    (silently skipped via INSERT OR IGNORE).

    Amount convention: negative = expense, positive = income/refund.
    """
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO transactions
            (date, amount, description, account_id, merchant_id, category_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (date, amount, description, account_id, merchant_id, category_id, notes),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all categories ordered by name."""
    return conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()