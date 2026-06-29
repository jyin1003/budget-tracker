"""
dashboard/data.py — Config loading and DB queries for the budget dashboard.
"""

import calendar
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = _ROOT / "dashboard_config.toml"

from config import DB_PATH


# ══════════════════════════════════════════════════════════════════
# TOML LOADER
# ══════════════════════════════════════════════════════════════════

def _load_toml(path: Path) -> dict:
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass
    return _parse_toml_minimal(path.read_text())


def _strip_comment(s: str) -> str:
    """Strip trailing inline comment from a value string, respecting quoted strings."""
    # If inside a string, don't strip
    s = s.strip()
    if s.startswith('"') or s.startswith("'"):
        return s
    # Strip trailing # comment (only if preceded by whitespace)
    return re.sub(r'\s+#.*$', '', s).strip()


def _parse_toml_minimal(text: str) -> dict:
    result: dict = {}
    current_section: list[str] = []
    array_key: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        m = re.match(r'^\[\[([^\]]+)\]\]$', line)
        if m:
            array_key = m.group(1).strip()
            if array_key not in result:
                result[array_key] = []
            result[array_key].append({})
            current_section = []
            continue

        m = re.match(r'^\[([^\]]+)\]$', line)
        if m:
            array_key = None
            current_section = [p.strip() for p in m.group(1).split(".")]
            node = result
            for part in current_section:
                node = node.setdefault(part, {})
            continue

        if "=" not in line:
            continue

        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()

        # Strip inline comment before any value parsing
        # But only outside of quoted strings and arrays
        # For arrays, strip comment after the closing bracket
        if val.startswith("["):
            # Find the closing bracket first, then strip comment after it
            bracket_end = val.rfind("]")
            if bracket_end != -1:
                val = val[:bracket_end + 1]  # keep only up to and including ]
            items = [v.strip().strip('"').strip("'") for v in val.strip("[]").split(",") if v.strip()]
            converted = []
            for item in items:
                try:
                    converted.append(float(item) if "." in item else int(item))
                except ValueError:
                    converted.append(item)
            parsed = converted
        elif val.startswith('"') or val.startswith("'"):
            parsed = val.strip('"').strip("'")
        elif val.lower() in ("true", "false"):
            parsed = val.lower() == "true"
        else:
            # Strip inline comment for scalar values
            val = _strip_comment(val)
            try:
                parsed = int(val)
            except ValueError:
                try:
                    parsed = float(val)
                except ValueError:
                    parsed = val

        if array_key is not None:
            result[array_key][-1][key] = parsed
        elif current_section:
            node = result
            for part in current_section:
                node = node.setdefault(part, {})
            node[key] = parsed
        else:
            result[key] = parsed

    return result


# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

@dataclass
class SectionConfig:
    name: str
    display: str
    categories: list[str]


@dataclass
class DashboardConfig:
    income_accounts: list[str]
    excluded_cats:   list[str]
    budgets:         dict[str, float]
    sections:        list[SectionConfig]


def load_config() -> DashboardConfig:
    raw = _load_toml(CONFIG_PATH)
    return DashboardConfig(
        income_accounts = raw.get("income", {}).get("accounts", []),
        excluded_cats   = raw.get("excluded", {}).get("categories", []),
        budgets         = {k: float(v) for k, v in raw.get("budgets", {}).items()},
        sections        = [
            SectionConfig(
                name       = s["name"],
                display    = s.get("display", "bar_comparison"),
                categories = s.get("categories", []),
            )
            for s in raw.get("sections", [])
        ],
    )


# ══════════════════════════════════════════════════════════════════
# DB
# ══════════════════════════════════════════════════════════════════

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def available_months() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT substr(date, 1, 7) as ym
            FROM transactions
            ORDER BY ym DESC
        """).fetchall()
    return [r["ym"] for r in rows]


def month_range(ym: str) -> tuple[str, str]:
    y, m = int(ym[:4]), int(ym[5:7])
    last = calendar.monthrange(y, m)[1]
    return f"{ym}-01", f"{ym}-{last:02d}"


def prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    if m == 1:
        return f"{y-1}-12"
    return f"{y}-{m-1:02d}"


def fetch_spending(ym: str, cfg: DashboardConfig) -> dict[str, float]:
    start, end = month_range(ym)
    excl = [c.lower() for c in cfg.excluded_cats]
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.name as category, SUM(ABS(t.amount)) as total
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            LEFT JOIN accounts a   ON a.id = t.account_id
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount < 0
              AND c.name IS NOT NULL
            GROUP BY c.id
            ORDER BY c.name
        """, (start, end)).fetchall()
    return {
        r["category"]: round(r["total"] or 0, 2)
        for r in rows
        if r["category"] and r["category"].lower() not in excl
    }


def fetch_income(ym: str, cfg: DashboardConfig) -> float:
    start, end = month_range(ym)
    accounts = cfg.income_accounts
    if not accounts:
        return 0.0
    placeholders = ",".join("?" * len(accounts))
    with get_conn() as conn:
        row = conn.execute(f"""
            SELECT COALESCE(SUM(t.amount), 0) as total
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount > 0
              AND a.name IN ({placeholders})
        """, [start, end] + accounts).fetchone()
    return round(float(row["total"]), 2)


def fetch_dashboard_data(ym: str, cfg: DashboardConfig) -> dict:
    prev_ym     = prev_month(ym)
    spending    = fetch_spending(ym, cfg)
    prev_spend  = fetch_spending(prev_ym, cfg)
    income      = fetch_income(ym, cfg)
    prev_income = fetch_income(prev_ym, cfg)

    sections_data = []
    for sec in cfg.sections:
        cats_data = []
        for cat in sec.categories:
            cats_data.append({
                "name":   cat,
                "amount": spending.get(cat, 0.0),
                "prev":   prev_spend.get(cat, 0.0),
                "budget": cfg.budgets.get(cat),
            })
        sections_data.append({
            "name":       sec.name,
            "display":    sec.display,
            "categories": cats_data,
        })

    excl_lower  = [c.lower() for c in cfg.excluded_cats]
    total_spend = round(sum(v for k, v in spending.items()   if k.lower() not in excl_lower), 2)
    prev_total  = round(sum(v for k, v in prev_spend.items() if k.lower() not in excl_lower), 2)

    return {
        "month":       ym,
        "prev_month":  prev_ym,
        "income":      income,
        "prev_income": prev_income,
        "total_spend": total_spend,
        "prev_total":  prev_total,
        "sections":    sections_data,
        "months":      available_months(),
    }