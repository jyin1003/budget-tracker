"""
ingestion/merchant_cli.py — Interactive CLI for categorising new merchants.

Called during ingestion whenever a raw description has no known alias.
Prompts the user to:
  1. Confirm or edit the merchant name (default: title-cased raw description)
  2. Pick an existing category, or create a new one on the spot
  3. Persists the merchant + alias so future occurrences are auto-resolved

Usage (imported by amex_ingest.py — not run directly):
    from ingestion.merchant_cli import resolve_or_create_merchant
    merchant_id, category_id = resolve_or_create_merchant(conn, raw_description)
"""

import sqlite3


# ── ANSI colour helpers (gracefully degrade on Windows) ──────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

DIM    = lambda t: _c("2", t)
BOLD   = lambda t: _c("1", t)
CYAN   = lambda t: _c("96", t)
YELLOW = lambda t: _c("93", t)
GREEN  = lambda t: _c("92", t)
RED    = lambda t: _c("91", t)


# ── Category helpers ──────────────────────────────────────────────────────────

def list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()


def create_category(conn: sqlite3.Connection, name: str) -> int:
    """Insert a new top-level category and return its id."""
    cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (name.strip(),))
    conn.commit()
    return cur.lastrowid


def _prompt_category(conn: sqlite3.Connection) -> int:
    """
    Show a numbered list of categories and let the user pick one,
    or type 'new' to create a fresh category on the spot.
    Returns the chosen category_id.
    """
    while True:
        categories = list_categories(conn)

        print()
        print(BOLD("  Categories:"))
        for i, row in enumerate(categories, start=1):
            print(f"    {DIM(str(i).rjust(2))}  {row['name']}")
        print(f"    {DIM(' N')}  {YELLOW('+ Add new category')}")
        print()

        raw = input(CYAN("  Pick a number or 'N' to add new: ")).strip().lower()

        if raw == "n":
            new_name = input(CYAN("  New category name: ")).strip()
            if not new_name:
                print(RED("  Category name cannot be empty."))
                continue
            # Check it doesn't already exist (case-insensitive)
            existing = conn.execute(
                "SELECT id FROM categories WHERE LOWER(name) = LOWER(?)", (new_name,)
            ).fetchone()
            if existing:
                print(YELLOW(f"  '{new_name}' already exists — using it."))
                return existing["id"]
            cat_id = create_category(conn, new_name)
            print(GREEN(f"  ✓ Created category '{new_name}'"))
            return cat_id

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(categories):
                return categories[idx]["id"]

        print(RED("  Invalid choice, try again."))


# ── Merchant upsert ───────────────────────────────────────────────────────────

def _upsert_merchant(conn: sqlite3.Connection, name: str, category_id: int) -> int:
    """Return merchant id, creating the row if needed."""
    row = conn.execute("SELECT id FROM merchants WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO merchants (name, category_id) VALUES (?, ?)", (name, category_id)
    )
    return cur.lastrowid


def _save_alias(conn: sqlite3.Connection, raw_description: str, merchant_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO merchant_aliases (raw_description, merchant_id) VALUES (?, ?)",
        (raw_description, merchant_id),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_or_create_merchant(
    conn: sqlite3.Connection,
    raw_description: str,
) -> tuple[int, int]:
    """
    Given a raw bank description that has no existing alias, interactively
    prompt the user to name the merchant and assign a category.

    Returns (merchant_id, category_id) — both already committed to the DB.

    Note: does NOT commit the alias itself; the caller (amex_ingest.py) does
    so inside its atomic batch transaction to keep rollback behaviour clean.
    The merchant + category rows are committed immediately so they persist
    even if ingestion is later interrupted (they're idempotent/harmless).
    """
    print()
    print("─" * 60)
    print(BOLD("  New merchant encountered"))
    print(f"  Raw description: {YELLOW(raw_description)}")
    print()

    # Default merchant name: title-case the raw description
    default_name = raw_description.title()
    name_input = input(
        CYAN(f"  Merchant name [{default_name}]: ")
    ).strip()
    merchant_name = name_input if name_input else default_name

    category_id = _prompt_category(conn)

    # Persist merchant (committed immediately — idempotent)
    merchant_id = _upsert_merchant(conn, merchant_name, category_id)
    conn.commit()

    # Stage the alias — will be committed by the caller's batch transaction
    _save_alias(conn, raw_description, merchant_id)

    cat_name = conn.execute(
        "SELECT name FROM categories WHERE id = ?", (category_id,)
    ).fetchone()["name"]

    print(GREEN(f"  ✓ Mapped '{raw_description}' → {merchant_name} ({cat_name})"))
    print("─" * 60)

    return merchant_id, category_id