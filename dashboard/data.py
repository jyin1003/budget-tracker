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
    s = s.strip()
    if s.startswith('"') or s.startswith("'"):
        return s
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

        if val.startswith("["):
            bracket_end = val.rfind("]")
            if bracket_end != -1:
                val = val[:bracket_end + 1]
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
    """
    Net spend per category for the given month.

    For each category: gross expenses (negative txs, abs'd) minus any credits
    (positive txs) in the same category. Floored at 0 so refunds never make a
    bar go negative. Excludes categories in cfg.excluded_cats.
    """
    start, end = month_range(ym)
    excl = [c.lower() for c in cfg.excluded_cats]
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                c.name as category,
                SUM(CASE WHEN t.amount < 0 THEN ABS(t.amount) ELSE 0 END) as gross_spend,
                SUM(CASE WHEN t.amount > 0 THEN t.amount      ELSE 0 END) as credits
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.date >= ? AND t.date <= ?
              AND c.name IS NOT NULL
            GROUP BY c.id
            ORDER BY c.name
        """, (start, end)).fetchall()
    return {
        r["category"]: round(max((r["gross_spend"] or 0) - (r["credits"] or 0), 0), 2)
        for r in rows
        if r["category"] and r["category"].lower() not in excl
    }


def fetch_income(ym: str, cfg: DashboardConfig) -> float:
    """
    Total income for the month: sum of all positive-amount transactions,
    excluding any categories in cfg.excluded_cats.
    """
    start, end = month_range(ym)
    excl = [c.lower() for c in cfg.excluded_cats]

    if excl:
        placeholders = ",".join("?" * len(excl))
        query = f"""
            SELECT COALESCE(SUM(t.amount), 0) as total
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount > 0
              AND (c.name IS NULL OR LOWER(c.name) NOT IN ({placeholders}))
        """
        params = [start, end] + excl
    else:
        query = """
            SELECT COALESCE(SUM(t.amount), 0) as total
            FROM transactions t
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount > 0
        """
        params = [start, end]

    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    return round(float(row["total"]), 2)

def fetch_total_spend(ym: str, cfg: DashboardConfig) -> float:
    """
    Total spending for the month: sum of ALL negative-amount transactions
    (returned as a positive number). Excludes cfg.excluded_cats.
    """
    start, end = month_range(ym)
    excl = [c.lower() for c in cfg.excluded_cats]

    if excl:
        placeholders = ",".join("?" * len(excl))
        query = f"""
            SELECT COALESCE(SUM(ABS(t.amount)), 0) as total
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount < 0
              AND (c.name IS NULL OR LOWER(c.name) NOT IN ({placeholders}))
        """
        params = [start, end] + excl
    else:
        query = """
            SELECT COALESCE(SUM(ABS(t.amount)), 0) as total
            FROM transactions t
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount < 0
        """
        params = [start, end]

    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    return round(float(row["total"]), 2)


def fetch_transactions_for_category(ym: str, category: str) -> list[dict]:
    """
    All transactions for a given month and category, sorted by date then amount.
    Includes both expenses and credits (refunds).
    """
    start, end = month_range(ym)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                t.date,
                t.amount,
                t.description,
                m.name AS merchant
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            LEFT JOIN merchants  m ON m.id = t.merchant_id
            WHERE t.date >= ? AND t.date <= ?
              AND c.name = ?
            ORDER BY t.date ASC, t.amount ASC
        """, (start, end, category)).fetchall()
    return [dict(r) for r in rows]


def fetch_income_transactions(ym: str, cfg: DashboardConfig) -> list[dict]:
    """
    All positive-amount transactions for the month, excluding cfg.excluded_cats.
    Sorted by date ascending, then amount descending.
    """
    start, end = month_range(ym)
    excl = [c.lower() for c in cfg.excluded_cats]

    if excl:
        placeholders = ",".join("?" * len(excl))
        query = f"""
            SELECT
                t.date,
                t.amount,
                t.description,
                m.name AS merchant,
                c.name AS category
            FROM transactions t
            LEFT JOIN merchants  m ON m.id = t.merchant_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount > 0
              AND (c.name IS NULL OR LOWER(c.name) NOT IN ({placeholders}))
            ORDER BY t.date ASC, t.amount DESC
        """
        params = [start, end] + excl
    else:
        query = """
            SELECT
                t.date,
                t.amount,
                t.description,
                m.name AS merchant,
                c.name AS category
            FROM transactions t
            LEFT JOIN merchants  m ON m.id = t.merchant_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount > 0
            ORDER BY t.date ASC, t.amount DESC
        """
        params = [start, end]

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]

def fetch_spend_transactions(ym: str, cfg: DashboardConfig) -> list[dict]:
    """
    All negative-amount transactions for the month (the full spend set).
    Excludes cfg.excluded_cats. Sorted by date ascending, then amount ascending.
    """
    start, end = month_range(ym)
    excl = [c.lower() for c in cfg.excluded_cats]

    if excl:
        placeholders = ",".join("?" * len(excl))
        query = f"""
            SELECT
                t.date,
                t.amount,
                t.description,
                m.name AS merchant,
                c.name AS category
            FROM transactions t
            LEFT JOIN merchants  m ON m.id = t.merchant_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount < 0
              AND (c.name IS NULL OR LOWER(c.name) NOT IN ({placeholders}))
            ORDER BY t.date ASC, t.amount ASC
        """
        params = [start, end] + excl
    else:
        query = """
            SELECT
                t.date,
                t.amount,
                t.description,
                m.name AS merchant,
                c.name AS category
            FROM transactions t
            LEFT JOIN merchants  m ON m.id = t.merchant_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.date >= ? AND t.date <= ?
              AND t.amount < 0
            ORDER BY t.date ASC, t.amount ASC
        """
        params = [start, end]

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def fetch_dashboard_data(ym: str, cfg: DashboardConfig) -> dict:
    prev_ym     = prev_month(ym)
    spending    = fetch_spending(ym, cfg)
    prev_spend  = fetch_spending(prev_ym, cfg)
    income      = fetch_income(ym, cfg)
    prev_income = fetch_income(prev_ym, cfg)
    total_spend = fetch_total_spend(ym, cfg)
    prev_total  = fetch_total_spend(prev_ym, cfg)

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