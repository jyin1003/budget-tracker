"""
ingestion/ingest.py — Main entry point for all bank statement ingestion.

Orchestrates the full pipeline:
    1. Parse   — each registered ingester scans its CSVs, skips already-
                 ingested files, parses rows, flags unknown merchants
    2. CLI     — all unknown merchants across ALL ingesters are resolved
                 interactively in one focused batch session
    3. Commit  — each ingester inserts its rows atomically (BEGIN/COMMIT)
                 using the resolved merchant mapping

Usage:
    python ingestion/ingest.py
    python ingestion/ingest.py --dry-run
"""

import sys
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db.db import get_conn, init_db
from db.db import resolve_merchant
from ingestion.merchant_cli import run_merchant_cli

# ── Ingester registry — add new ingesters here ───────────────────────────────
from ingestion import amex_ingest

INGESTERS = [
    amex_ingest,
    # nab_ingest,
    # ing_ingest,
]

# ── ANSI helpers ──────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

BOLD  = lambda t: _c("1", t)
BLUE  = lambda t: _c("94", t)
GREEN = lambda t: _c("92", t)
DIM   = lambda t: _c("2", t)
YELLOW= lambda t: _c("93", t)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest all bank statements.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and preview without writing to DB.")
    args = parser.parse_args()

    print(BOLD(BLUE("\n═══ Budget Tracker — Ingestion Pipeline ═══")))
    if args.dry_run:
        print(YELLOW("  DRY RUN — nothing will be written\n"))

    init_db()

    conn = get_conn()
    try: 
        # ── Phase 1: Parse ────────────────────────────────────────────────
        print(BOLD("\n── Phase 1: Parsing statements ──"))

        # staged_by_ingester: list of (ingester_module, list[StagedFile])
        staged_by_ingester = []
        # Collect ALL unknown raw descriptions across every ingester,
        # deduplicated — same raw string from two ingesters only prompts once.
        all_unknowns_ordered: list[str] = []
        seen_unknowns: set[str] = set()

        for ingester in INGESTERS:
            staged_files = ingester.parse(conn)
            staged_by_ingester.append((ingester, staged_files))

            for sf in staged_files:
                for raw in sf.unknowns:
                    if raw not in seen_unknowns:
                        # Double-check: another ingester's commit may have
                        # already added this alias in a prior iteration
                        if resolve_merchant(conn, raw) is None:
                            seen_unknowns.add(raw)
                            all_unknowns_ordered.append(raw)

        if not any(sf for _, files in staged_by_ingester for sf in files):
            print(GREEN("\n  Nothing to ingest — all statements up to date."))
            return

        # ── Phase 2: Merchant CLI ─────────────────────────────────────────
        print(BOLD("\n── Phase 2: Merchant categorisation ──"))
        resolved = run_merchant_cli(conn, all_unknowns_ordered)

        # ── Phase 3: Commit ───────────────────────────────────────────────
        print(BOLD("── Phase 3: Committing transactions ──"))

        grand_inserted = 0
        grand_skipped  = 0
        grand_errors   = 0

        for ingester, staged_files in staged_by_ingester:
            if not staged_files:
                continue
            summary = ingester.commit(conn, staged_files, resolved, dry_run=args.dry_run)
            grand_inserted += summary["inserted"]
            grand_skipped  += summary["skipped"]
            grand_errors   += summary["errors"]

        # ── Summary ───────────────────────────────────────────────────────
        print()
        print(BOLD(BLUE("═══ Complete ═══")))
        print(f"  Rows inserted : {GREEN(str(grand_inserted))}")
        print(f"  Rows skipped  : {DIM(str(grand_skipped))}")
        if grand_errors:
            print(f"  Errors        : \033[91m{grand_errors}\033[0m")
        print()
    finally:
        conn.close()

if __name__ == "__main__":
    main()