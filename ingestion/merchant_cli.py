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
from ingestion.merchant_match import normalise, find_candidates


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

    Flow:
      - If candidates exist, show numbered list with scores.
          number  → accept that candidate
          Enter   → drop into name prompt
          text    → use as merchant name, drop into category prompt
      - Name prompt: pre-filled with normalise(raw), Enter accepts default.
      - Category prompt: standard numbered list.
    """
    print()
    print("─" * 64)
    print(BOLD(f"  [{index}/{total}] New merchant"))
    print(f"  Raw : {YELLOW(raw)}")

    candidates = find_candidates(conn, raw)
    proposed_name = normalise(raw)

    # ── Show candidates if any ────────────────────────────────────────────────
    if candidates:
        print()
        print(BOLD("  Possible matches:"))
        for i, (mid, mname, mcatid, score) in enumerate(candidates, start=1):
            cat_name = conn.execute(
                "SELECT name FROM categories WHERE id = ?", (mcatid,)
            ).fetchone()
            cat_label = cat_name["name"] if cat_name else "?"
            print(
                f"    {CYAN(str(i))}  {mname:<30} {DIM(f'({cat_label})'):<20}  {score}%"
            )
        print()
        print(DIM(f"  Enter a number to accept, or press Enter / type a name to override [{proposed_name}]:"))
        raw_input = input(CYAN("  > ")).strip()
    else:
        print(DIM(f"  No close matches found."))
        print(DIM(f"  Press Enter to accept suggested name, or type a custom one:"))
        raw_input = input(CYAN(f"  Merchant name [{proposed_name}]: ")).strip()

    # ── Parse response ────────────────────────────────────────────────────────

    # Numeric input with candidates → accept that candidate
    if candidates and raw_input.isdigit():
        idx = int(raw_input) - 1
        if 0 <= idx < len(candidates):
            mid, mname, mcatid, _ = candidates[idx]
            _stage_alias(conn, raw, mid)
            cat_label = conn.execute(
                "SELECT name FROM categories WHERE id = ?", (mcatid,)
            ).fetchone()["name"]
            print(GREEN(f"  ✓ Mapped → {mname} ({cat_label})"))
            print("─" * 64)
            return mid, mcatid
        # Out-of-range number: fall through to name prompt
        print(YELLOW(f"  Invalid number — falling through to name prompt."))
        raw_input = ""

    # Determine the merchant name:
    #   - typed text (non-numeric) → use as-is, title-cased
    #   - Enter (empty) with candidates → ask for name with default
    #   - Enter (empty) without candidates → raw_input already IS the name
    #     input captured above (may be empty → use proposed_name)
    if raw_input:
        # Typed text → use directly, skip re-prompt
        merchant_name = raw_input.strip().title()
    elif candidates:
        # Enter after seeing candidates → still need to ask for name
        name_input = input(CYAN(f"  Merchant name [{proposed_name}]: ")).strip()
        merchant_name = name_input if name_input else proposed_name
    else:
        # No candidates branch — raw_input was already the name prompt response
        # (may have text if user typed, or "" if they hit Enter → use default)
        merchant_name = raw_input.strip().title() if raw_input else proposed_name

    preselect = candidates[0][2] if candidates else None
    category_id = _prompt_category(conn, preselect_id=preselect)
    merchant_id = _upsert_merchant(conn, merchant_name, category_id)
    conn.commit()
    _stage_alias(conn, raw, merchant_id)

    cat_label = conn.execute(
        "SELECT name FROM categories WHERE id = ?", (category_id,)
    ).fetchone()["name"]
    print(GREEN(f"  ✓ Mapped '{raw}' → {merchant_name} ({cat_label})"))
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