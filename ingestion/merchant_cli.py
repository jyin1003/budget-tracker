"""
ingestion/merchant_cli.py — Batch interactive CLI for categorising new merchants.

Called once by ingest.py after all ingesters have parsed their CSVs, with a
deduplicated list of unknown raw descriptions across all sources.

Public API:
    run_merchant_cli(conn, unknowns) -> dict[raw_description, (merchant_id, category_id)]

    unknowns: list[str]  — deduplicated raw descriptions with no existing alias
    returns:  mapping used by each ingester to resolve merchant/category ids
                before committing their transactions.
"""

import sqlite3
from ingestion.merchant_match import normalise, find_fuzzy_match


# ── ANSI colour helpers ───────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

DIM    = lambda t: _c("2", t)
BOLD   = lambda t: _c("1", t)
CYAN   = lambda t: _c("96", t)
YELLOW = lambda t: _c("93", t)
GREEN  = lambda t: _c("92", t)
RED    = lambda t: _c("91", t)


# ── Category helpers ──────────────────────────────────────────────────────────

def _list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()


def _create_category(conn: sqlite3.Connection, name: str) -> int:
    existing = conn.execute(
        "SELECT id FROM categories WHERE LOWER(name) = LOWER(?)", (name.strip(),)
    ).fetchone()
    if existing:
        print(YELLOW(f"  '{name}' already exists — using it."))
        return existing["id"]
    cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (name.strip(),))
    conn.commit()
    print(GREEN(f"  ✓ Created category '{name}'"))
    return cur.lastrowid


def _prompt_category(conn: sqlite3.Connection, preselect_id: int | None = None) -> int:
    """
    Display category list and return chosen category_id.
    If preselect_id is given, highlights it as the suggestion.
    """
    while True:
        categories = _list_categories(conn)

        print()
        print(BOLD("  Categories:"))
        for i, row in enumerate(categories, start=1):
            marker = GREEN(" ◀ suggested") if row["id"] == preselect_id else ""
            print(f"    {DIM(str(i).rjust(2))}  {row['name']}{marker}")
        print(f"    {DIM(' N')}  {YELLOW('+ Add new category')}")

        if preselect_id:
            print()
            hint = next((r["name"] for r in categories if r["id"] == preselect_id), "")
            raw = input(CYAN(f"  Pick a number, 'N' to add new [Enter = {hint}]: ")).strip().lower()
            if raw == "":
                return preselect_id
        else:
            print()
            raw = input(CYAN("  Pick a number or 'N' to add new: ")).strip().lower()

        if raw == "n":
            new_name = input(CYAN("  New category name: ")).strip()
            if not new_name:
                print(RED("  Category name cannot be empty."))
                continue
            return _create_category(conn, new_name)

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(categories):
                return categories[idx]["id"]

        print(RED("  Invalid choice, try again."))


# ── Merchant upsert ───────────────────────────────────────────────────────────

def _upsert_merchant(conn: sqlite3.Connection, name: str, category_id: int) -> int:
    row = conn.execute(
        "SELECT id FROM merchants WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO merchants (name, category_id) VALUES (?, ?)", (name, category_id)
    )
    conn.commit()
    return cur.lastrowid


def _stage_alias(conn: sqlite3.Connection, raw: str, merchant_id: int) -> None:
    """Stage alias — not committed yet; caller's batch transaction handles that."""
    conn.execute(
        "INSERT OR IGNORE INTO merchant_aliases (raw_description, merchant_id) VALUES (?, ?)",
        (raw, merchant_id),
    )


# ── Single merchant prompt ────────────────────────────────────────────────────

def _prompt_one(
    conn: sqlite3.Connection,
    raw: str,
    index: int,
    total: int,
) -> tuple[int, int]:
    """
    Resolve one unknown raw description interactively.
    Returns (merchant_id, category_id) — merchant committed, alias staged.
    """
    print()
    print("─" * 64)
    print(BOLD(f"  [{index}/{total}] New merchant"))
    print(f"  Raw : {YELLOW(raw)}")

    # Try fuzzy match against existing merchants
    fuzzy = find_fuzzy_match(conn, raw)
    proposed_name = normalise(raw)

    if fuzzy:
        fuzzy_id, fuzzy_name, fuzzy_cat_id = fuzzy
        fuzzy_cat = conn.execute(
            "SELECT name FROM categories WHERE id = ?", (fuzzy_cat_id,)
        ).fetchone()["name"]
        print(f"  {GREEN('Possible match:')} {fuzzy_name} {DIM(f'({fuzzy_cat})')}")
        print()
        print(f"    {DIM('Y')}  Use '{fuzzy_name}'  {DIM('(suggested match)')}")
        print(f"    {DIM('N')}  Enter a different name  {DIM(f'(default: {proposed_name})')}")
        print()
        choice = input(CYAN("  Accept match? [Y/n]: ")).strip().lower()

        if choice in ("", "y"):
            # Accept fuzzy match — just stage alias, no new merchant needed
            _stage_alias(conn, raw, fuzzy_id)
            print(GREEN(f"  ✓ Mapped → {fuzzy_name} ({fuzzy_cat})"))
            print("─" * 64)
            return fuzzy_id, fuzzy_cat_id

    # No fuzzy match accepted — prompt for name
    print(DIM("  Enter to accept suggested name, or type a custom one:"))
    name_input = input(CYAN(f"  Merchant name [{proposed_name}]: ")).strip()
    merchant_name = name_input if name_input else proposed_name

    # Prompt for category (pre-select fuzzy cat if we had a partial match)
    preselect = fuzzy[2] if fuzzy else None
    category_id = _prompt_category(conn, preselect_id=preselect)

    merchant_id = _upsert_merchant(conn, merchant_name, category_id)
    conn.commit()

    _stage_alias(conn, raw, merchant_id)

    cat_name = conn.execute(
        "SELECT name FROM categories WHERE id = ?", (category_id,)
    ).fetchone()["name"]
    print(GREEN(f"  ✓ Mapped '{raw}' → {merchant_name} ({cat_name})"))
    print("─" * 64)

    return merchant_id, category_id


# ── Public entry point ────────────────────────────────────────────────────────

def run_merchant_cli(
    conn: sqlite3.Connection,
    unknowns: list[str],
) -> dict[str, tuple[int, int]]:
    """
    Run the interactive CLI for all unknown raw descriptions in one batch.

    Args:
        conn:     DB connection (used for merchant/category lookups & inserts)
        unknowns: deduplicated list of raw descriptions with no existing alias

    Returns:
        dict mapping raw_description -> (merchant_id, category_id)
        Aliases are staged (INSERT OR IGNORE) but not yet committed —
        each ingester's atomic batch transaction will commit them.
    """
    if not unknowns:
        return {}

    print()
    print(BOLD(f"  {len(unknowns)} unknown merchant(s) to categorise"))

    resolved: dict[str, tuple[int, int]] = {}
    total = len(unknowns)

    for i, raw in enumerate(unknowns, start=1):
        merchant_id, category_id = _prompt_one(conn, raw, i, total)
        resolved[raw] = (merchant_id, category_id)

    print()
    print(GREEN(BOLD(f"  ✓ All {total} merchant(s) resolved — proceeding to commit.")))
    print()

    return resolved