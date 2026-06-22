"""
ingestion/rules.py — Post-commit rule engine for transaction overrides.

Runs AFTER all ingestion and merchant processing. Queries committed transactions
directly and issues UPDATE statements against the DB. No ingester code is touched.

Rules are defined in rules.toml at the project root. Each rule can match on
any combination of: account name (substring), merchant name or raw description
(substring or exact), and amount (exact, range, or list). On match, the rule
overrides category_id and/or merchant_id.

Rules are evaluated in order; first match wins per transaction.

Public API:
    load_rules()          → list[Rule]
    apply_rules(conn)     → int  (number of transactions updated)
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent if _HERE.name == "ingestion" else _HERE
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── ANSI helpers ──────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

BOLD   = lambda t: _c("1", t)
DIM    = lambda t: _c("2", t)
CYAN   = lambda t: _c("96", t)
YELLOW = lambda t: _c("93", t)
GREEN  = lambda t: _c("92", t)
RED    = lambda t: _c("91", t)


# ── TOML parser (stdlib 3.11+; minimal fallback for older) ───────────────────

def _load_toml(path: Path) -> dict:
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass
    try:
        import tomli
        with open(path, "rb") as f:
            return tomli.load(f)
    except ImportError:
        pass
    return _parse_toml_minimal(path.read_text())


def _parse_toml_minimal(text: str) -> dict:
    """Minimal [[rules]] TOML parser — no third-party deps required."""
    rules = []
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[rules]]":
            if current is not None:
                rules.append(current)
            current = {}
            continue
        if current is None or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = re.sub(r'\s+#.*$', '', val.strip())
        if val.startswith('"') or val.startswith("'"):
            current[key] = val.strip('"').strip("'")
        elif val.startswith("["):
            items = [v.strip().strip('"').strip("'") for v in val.strip("[]").split(",") if v.strip()]
            converted = []
            for item in items:
                try:
                    converted.append(float(item))
                except ValueError:
                    converted.append(item)
            current[key] = converted
        else:
            try:
                current[key] = float(val)
            except ValueError:
                current[key] = val
    if current is not None:
        rules.append(current)
    return {"rules": rules}


# ── Rule dataclass ────────────────────────────────────────────────────────────

@dataclass
class Rule:
    name: str = ""

    # Match conditions (all specified must match — AND logic)
    account_contains:     str | None = None   # substring of account name
    merchant_contains:    str | None = None   # substring of resolved merchant name
    description_contains: str | None = None   # substring of raw description
    amount_exact:         list[float] = field(default_factory=list)  # abs(amount) in list
    amount_min:           float | None = None
    amount_max:           float | None = None

    # Override actions
    set_category: str | None = None   # category name (must exist in DB)
    set_merchant: str | None = None   # merchant name (must exist in DB)


# ── Load rules ────────────────────────────────────────────────────────────────

def load_rules(path: Path | None = None) -> list[Rule]:
    if path is None:
        path = _ROOT / "rules.toml"
    if not path.exists():
        return []

    raw = _load_toml(path)
    rules: list[Rule] = []
    for i, entry in enumerate(raw.get("rules", [])):
        try:
            rule = Rule(
                name                 = str(entry.get("name", f"rule_{i+1}")),
                account_contains     = entry.get("account_contains") or None,
                merchant_contains    = entry.get("merchant_contains") or None,
                description_contains = entry.get("description_contains") or None,
                amount_exact         = [float(x) for x in entry.get("amount_exact", [])],
                amount_min           = float(entry["amount_min"]) if "amount_min" in entry else None,
                amount_max           = float(entry["amount_max"]) if "amount_max" in entry else None,
                set_category         = entry.get("set_category") or None,
                set_merchant         = entry.get("set_merchant") or None,
            )
            if not rule.set_category and not rule.set_merchant:
                print(YELLOW(f"  [rules] '{rule.name}' has no set_category or set_merchant — skipped"))
                continue
            rules.append(rule)
        except (KeyError, ValueError, TypeError) as e:
            print(YELLOW(f"  [rules] Rule {i+1} invalid — {e} — skipped"))

    return rules


# ── Apply rules ───────────────────────────────────────────────────────────────

def apply_rules(conn, rules: list[Rule] | None = None, since: str | None = None) -> int:
    """
    Query committed transactions and UPDATE any that match a rule.

    since: UTC timestamp string 'YYYY-MM-DD HH:MM:SS' — only rows with
           created_at >= this value are evaluated. Pass the timestamp captured
           at the start of the ingest run to restrict to newly inserted rows.
           If None, all transactions are evaluated.

    First matching rule wins per transaction. Returns total rows updated.
    """
    if rules is None:
        rules = load_rules()
    if not rules:
        print(f"  {DIM('[rules]')} No rules defined — skipping.")
        return 0

    # Resolve category/merchant names → ids once up front
    cat_ids: dict[str, int] = {
        r["name"].lower(): r["id"]
        for r in conn.execute("SELECT id, name FROM categories").fetchall()
    }
    merch_ids: dict[str, int] = {
        r["name"].lower(): r["id"]
        for r in conn.execute("SELECT id, name FROM merchants").fetchall()
    }
    merch_cat: dict[int, int] = {
        r["id"]: r["category_id"]
        for r in conn.execute("SELECT id, category_id FROM merchants").fetchall()
    }

    # Validate rules against DB
    validated: list[tuple[Rule, int | None, int | None]] = []
    for rule in rules:
        mid = cid = None
        if rule.set_merchant:
            mid = merch_ids.get(rule.set_merchant.lower())
            if mid is None:
                print(YELLOW(f"  [rules] '{rule.name}': merchant '{rule.set_merchant}' not in DB — will skip merchant override"))
            else:
                if not rule.set_category:
                    cid = merch_cat.get(mid)
        if rule.set_category:
            cid = cat_ids.get(rule.set_category.lower())
            if cid is None:
                print(YELLOW(f"  [rules] '{rule.name}': category '{rule.set_category}' not in DB — will skip category override"))
        validated.append((rule, mid, cid))

    # Fetch transactions — scoped to new rows if since is provided
    since_clause = "WHERE t.created_at >= ?" if since else ""
    since_args   = [since] if since else []

    rows = conn.execute(f"""
        SELECT
            t.id,
            t.amount,
            t.description,
            t.merchant_id,
            t.category_id,
            m.name AS merchant_name,
            a.name AS account_name
        FROM transactions t
        LEFT JOIN merchants  m ON m.id = t.merchant_id
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN accounts   a ON a.id = t.account_id
        {since_clause}
    """, since_args).fetchall()

    if not rows:
        print(f"  {DIM('[rules]')} No new transactions to evaluate.")
        return 0

    total_updated = 0
    rule_hits: dict[str, int] = {}

    for tx in rows:
        abs_amt     = abs(tx["amount"])
        desc_lower  = (tx["description"] or "").lower()
        merch_lower = (tx["merchant_name"] or "").lower()
        acc_lower   = (tx["account_name"] or "").lower()

        for rule, new_mid, new_cid in validated:
            if rule.account_contains and rule.account_contains.lower() not in acc_lower:
                continue
            if rule.merchant_contains and rule.merchant_contains.lower() not in merch_lower:
                continue
            if rule.description_contains and rule.description_contains.lower() not in desc_lower:
                continue
            if rule.amount_exact:
                if not any(abs(abs_amt - ex) < 0.005 for ex in rule.amount_exact):
                    continue
            if rule.amount_min is not None and abs_amt < rule.amount_min:
                continue
            if rule.amount_max is not None and abs_amt > rule.amount_max:
                continue

            updates: dict[str, int] = {}
            if new_mid is not None and new_mid != tx["merchant_id"]:
                updates["merchant_id"] = new_mid
            if new_cid is not None and new_cid != tx["category_id"]:
                updates["category_id"] = new_cid

            if updates:
                set_clause = ", ".join(f"{col} = ?" for col in updates)
                conn.execute(
                    f"UPDATE transactions SET {set_clause} WHERE id = ?",
                    list(updates.values()) + [tx["id"]],
                )
                total_updated += 1
                rule_hits[rule.name] = rule_hits.get(rule.name, 0) + 1

            break  # first match wins

    if total_updated:
        print(f"  {CYAN('[rules]')} {total_updated} transaction(s) updated:")
        for rule_name, count in rule_hits.items():
            print(f"    {GREEN('✓')} {rule_name}: {count} row(s)")
    else:
        print(f"  {DIM('[rules]')} No transactions matched any rules.")

    return total_updated