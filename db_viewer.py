"""
db/db_viewer.py — Lightweight browser UI for budget.db.

Zero external dependencies — uses only Python stdlib (http.server, sqlite3, json).

Usage:
    python db_viewer.py          # opens on http://localhost:8765
    python db_viewer.py --port 9000
"""

import argparse
import json
import sqlite3
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import DB_PATH

# ── HTML ─────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Budget DB</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0d0f12;
    --surface:   #13161b;
    --border:    #1e2228;
    --border2:   #2a2f38;
    --text:      #c8d0dc;
    --muted:     #5a6275;
    --accent:    #4fffb0;
    --accent2:   #ff6b6b;
    --accent3:   #ffd166;
    --mono:      'IBM Plex Mono', monospace;
    --sans:      'IBM Plex Sans', sans-serif;
  }

  html, body { height: 100%; background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 14px; }

  /* ── layout ── */
  #app { display: grid; grid-template-rows: auto 1fr; height: 100vh; }

  header {
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    display: flex; align-items: center; gap: 32px;
    height: 52px;
    background: var(--surface);
  }
  .logo { font-family: var(--mono); font-weight: 600; font-size: 13px; color: var(--accent); letter-spacing: 0.08em; white-space: nowrap; }
  .logo span { color: var(--muted); }

  nav { display: flex; gap: 2px; }
  nav button {
    background: none; border: none; cursor: pointer;
    font-family: var(--mono); font-size: 12px; font-weight: 500;
    color: var(--muted); padding: 6px 14px; border-radius: 4px;
    letter-spacing: 0.04em; transition: color .15s, background .15s;
  }
  nav button:hover { color: var(--text); background: var(--border); }
  nav button.active { color: var(--accent); background: rgba(79,255,176,.08); }

  #status-bar {
    margin-left: auto; font-family: var(--mono); font-size: 11px;
    color: var(--muted); white-space: nowrap;
  }
  #status-bar .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); margin-right: 6px; animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

  main { overflow: hidden; display: flex; flex-direction: column; }

  /* ── toolbar ── */
  .toolbar {
    padding: 12px 24px; border-bottom: 1px solid var(--border);
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
  }
  .toolbar input, .toolbar select {
    background: var(--bg); border: 1px solid var(--border2);
    color: var(--text); font-family: var(--mono); font-size: 12px;
    padding: 6px 10px; border-radius: 4px; outline: none;
    transition: border-color .15s;
  }
  .toolbar input:focus, .toolbar select:focus { border-color: var(--accent); }
  .toolbar input { width: 220px; }
  .toolbar select { min-width: 140px; }
  .toolbar label { font-family: var(--mono); font-size: 11px; color: var(--muted); }

  .btn {
    background: rgba(79,255,176,.1); border: 1px solid rgba(79,255,176,.25);
    color: var(--accent); font-family: var(--mono); font-size: 12px;
    padding: 6px 14px; border-radius: 4px; cursor: pointer; transition: background .15s;
  }
  .btn:hover { background: rgba(79,255,176,.18); }
  .btn.ghost { background: none; border-color: var(--border2); color: var(--muted); }
  .btn.ghost:hover { border-color: var(--text); color: var(--text); }

  /* ── table ── */
  .table-wrap { flex: 1; overflow: auto; padding: 0 24px 16px; }

  table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }
  thead th {
    position: sticky; top: 0; background: var(--bg);
    text-align: left; padding: 10px 12px 10px 0;
    font-weight: 500; font-size: 11px; letter-spacing: 0.07em; text-transform: uppercase;
    color: var(--muted); border-bottom: 1px solid var(--border);
    white-space: nowrap; cursor: pointer; user-select: none;
  }
  thead th:hover { color: var(--text); }
  thead th .sort-arrow { margin-left: 4px; opacity: .4; }
  thead th.sorted .sort-arrow { opacity: 1; color: var(--accent); }

  tbody tr { transition: background .1s; }
  tbody tr:hover { background: rgba(255,255,255,.03); }
  tbody td { padding: 8px 12px 8px 0; border-bottom: 1px solid var(--border); vertical-align: middle; }

  .amount-neg { color: var(--accent2); }
  .amount-pos { color: var(--accent); }
  .pill {
    display: inline-block; padding: 2px 8px; border-radius: 20px;
    font-size: 10px; font-weight: 500; letter-spacing: 0.04em;
    background: rgba(79,255,176,.1); color: var(--accent); border: 1px solid rgba(79,255,176,.2);
  }
  .pill.cat { background: rgba(255,209,102,.08); color: var(--accent3); border-color: rgba(255,209,102,.2); }
  .pill.muted { background: rgba(90,98,117,.15); color: var(--muted); border-color: var(--border2); }

  /* ── pagination ── */
  .pagination {
    padding: 10px 24px; border-top: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px;
    font-family: var(--mono); font-size: 11px; color: var(--muted);
    background: var(--surface);
  }
  .pagination button { background: none; border: 1px solid var(--border2); color: var(--muted); font-family: var(--mono); font-size: 11px; padding: 3px 10px; border-radius: 3px; cursor: pointer; }
  .pagination button:hover:not(:disabled) { border-color: var(--text); color: var(--text); }
  .pagination button:disabled { opacity: .3; cursor: default; }
  .page-info { margin: 0 4px; }

  /* ── empty / loading ── */
  .empty { padding: 60px; text-align: center; color: var(--muted); font-family: var(--mono); font-size: 12px; }
  .loading { opacity: .5; }

  /* ── scrollbar ── */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
</style>
</head>
<body>
<div id="app">
  <header>
    <div class="logo">budget<span>.</span>db</div>
    <nav>
      <button class="active" onclick="switchTab('transactions')">transactions</button>
      <button onclick="switchTab('merchants')">merchants</button>
      <button onclick="switchTab('categories')">categories</button>
    </nav>
    <div id="status-bar"><span class="dot"></span><span id="db-path"></span></div>
  </header>

  <main id="main-content">
    <!-- injected by JS -->
  </main>
</div>

<script>
// ── state ─────────────────────────────────────────────────────────────────
const state = {
  tab: 'transactions',
  page: 1,
  pageSize: 50,
  total: 0,
  sortCol: 'date',
  sortDir: 'desc',
  filters: { q: '', category: '', account: '', amountMin: '', amountMax: '' },
  data: [],
  meta: {},
};

// ── API ───────────────────────────────────────────────────────────────────
async function api(endpoint, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const r = await fetch(`/api/${endpoint}${qs ? '?' + qs : ''}`);
  return r.json();
}

// ── tab switching ─────────────────────────────────────────────────────────
function switchTab(tab) {
  state.tab = tab;
  state.page = 1;
  state.sortCol = tab === 'transactions' ? 'date' : 'name';
  state.sortDir = tab === 'transactions' ? 'desc' : 'asc';
  state.filters = { q: '', category: '', account: '', amountMin: '', amountMax: '' };
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('nav button')[['transactions','merchants','categories'].indexOf(tab)].classList.add('active');
  renderTab();
}

// ── render dispatcher ─────────────────────────────────────────────────────
function renderTab() {
  if (state.tab === 'transactions') renderTransactions();
  else if (state.tab === 'merchants') renderMerchants();
  else renderCategories();
}

// ════════════════════════════════════════════════════════════════
// TRANSACTIONS
// ════════════════════════════════════════════════════════════════
async function renderTransactions() {
  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="empty loading">loading…</div>';

  const meta = await api('meta');
  document.getElementById('db-path').textContent = meta.db_path;

  const params = {
    page: state.page, page_size: state.pageSize,
    sort: state.sortCol, dir: state.sortDir,
    q: state.filters.q,
    category: state.filters.category,
    account: state.filters.account,
    amount_min: state.filters.amountMin,
    amount_max: state.filters.amountMax,
  };
  const res = await api('transactions', params);
  state.total = res.total;

  const cats = meta.categories.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
  const accs = meta.accounts.map(a => `<option value="${a.id}">${a.name}</option>`).join('');

  const cols = [
    { key: 'date',        label: 'Date' },
    { key: 'description', label: 'Description' },
    { key: 'merchant',    label: 'Merchant' },
    { key: 'category',    label: 'Category' },
    { key: 'account',     label: 'Account' },
    { key: 'amount',      label: 'Amount' },
  ];

  const thead = cols.map(c => {
    const sorted = state.sortCol === c.key ? 'sorted' : '';
    const arrow = state.sortCol === c.key ? (state.sortDir === 'asc' ? '↑' : '↓') : '↕';
    return `<th class="${sorted}" onclick="sortBy('${c.key}')">${c.label} <span class="sort-arrow">${arrow}</span></th>`;
  }).join('');

  const tbody = res.rows.length === 0
    ? `<tr><td colspan="6" class="empty">no transactions match</td></tr>`
    : res.rows.map(row => {
        const amt = row.amount;
        const amtClass = amt < 0 ? 'amount-neg' : 'amount-pos';
        const amtStr = (amt < 0 ? '-' : '+') + '$' + Math.abs(amt).toFixed(2);
        const merchant = row.merchant ? `<span class="pill">${esc(row.merchant)}</span>` : `<span class="pill muted">—</span>`;
        const cat = row.category ? `<span class="pill cat">${esc(row.category)}</span>` : `<span class="pill muted">—</span>`;
        return `<tr>
          <td>${row.date}</td>
          <td title="${esc(row.description)}">${esc(row.description.substring(0,55))}${row.description.length>55?'…':''}</td>
          <td>${merchant}</td>
          <td>${cat}</td>
          <td>${esc(row.account)}</td>
          <td class="${amtClass}" style="text-align:right;padding-right:0">${amtStr}</td>
        </tr>`;
      }).join('');

  const totalPages = Math.ceil(state.total / state.pageSize);
  const start = (state.page - 1) * state.pageSize + 1;
  const end = Math.min(state.page * state.pageSize, state.total);

  main.innerHTML = `
    <div class="toolbar">
      <input id="q" type="search" placeholder="search description / merchant…" value="${esc(state.filters.q)}" oninput="debounceFilter()">
      <select id="filter-cat" onchange="applyFilters()">
        <option value="">all categories</option>${cats}
      </select>
      <select id="filter-acc" onchange="applyFilters()">
        <option value="">all accounts</option>${accs}
      </select>
      <label>min $<input id="amt-min" type="number" style="width:80px" placeholder="0" value="${state.filters.amountMin}" oninput="debounceFilter()"></label>
      <label>max $<input id="amt-max" type="number" style="width:80px" placeholder="∞" value="${state.filters.amountMax}" oninput="debounceFilter()"></label>
      <button class="btn ghost" onclick="clearFilters()">clear</button>
      <span style="margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--muted)">${state.total.toLocaleString()} rows</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>${thead}</tr></thead>
        <tbody>${tbody}</tbody>
      </table>
    </div>
    <div class="pagination">
      <button onclick="goPage(1)" ${state.page===1?'disabled':''}>«</button>
      <button onclick="goPage(${state.page-1})" ${state.page===1?'disabled':''}>‹</button>
      <span class="page-info">${start}–${end} of ${state.total.toLocaleString()}</span>
      <button onclick="goPage(${state.page+1})" ${state.page>=totalPages?'disabled':''}>›</button>
      <button onclick="goPage(${totalPages})" ${state.page>=totalPages?'disabled':''}>»</button>
      <select onchange="state.pageSize=+this.value;state.page=1;renderTab()" style="margin-left:8px">
        ${[25,50,100,250].map(n=>`<option value="${n}" ${state.pageSize===n?'selected':''}>${n} / page</option>`).join('')}
      </select>
    </div>
  `;

  // restore filter values (select boxes)
  if (state.filters.category) document.getElementById('filter-cat').value = state.filters.category;
  if (state.filters.account)  document.getElementById('filter-acc').value = state.filters.account;
}

// ════════════════════════════════════════════════════════════════
// MERCHANTS
// ════════════════════════════════════════════════════════════════
async function renderMerchants() {
  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="empty loading">loading…</div>';

  const params = { page: state.page, page_size: state.pageSize, sort: state.sortCol, dir: state.sortDir, q: state.filters.q };
  const res = await api('merchants', params);
  state.total = res.total;

  const cols = [
    { key: 'name',       label: 'Merchant' },
    { key: 'category',   label: 'Category' },
    { key: 'alias_count',label: 'Aliases' },
    { key: 'tx_count',   label: 'Transactions' },
    { key: 'created_at', label: 'Added' },
  ];

  const thead = cols.map(c => {
    const sorted = state.sortCol === c.key ? 'sorted' : '';
    const arrow = state.sortCol === c.key ? (state.sortDir === 'asc' ? '↑' : '↓') : '↕';
    return `<th class="${sorted}" onclick="sortBy('${c.key}')">${c.label} <span class="sort-arrow">${arrow}</span></th>`;
  }).join('');

  const tbody = res.rows.length === 0
    ? `<tr><td colspan="5" class="empty">no merchants yet</td></tr>`
    : res.rows.map(row => `<tr>
        <td><strong>${esc(row.name)}</strong></td>
        <td><span class="pill cat">${esc(row.category)}</span></td>
        <td style="color:var(--muted)">${row.alias_count}</td>
        <td style="color:var(--muted)">${row.tx_count}</td>
        <td style="color:var(--muted)">${row.created_at.slice(0,10)}</td>
      </tr>`).join('');

  const totalPages = Math.ceil(state.total / state.pageSize);
  const start = (state.page - 1) * state.pageSize + 1;
  const end = Math.min(state.page * state.pageSize, state.total);

  main.innerHTML = `
    <div class="toolbar">
      <input id="q" type="search" placeholder="search merchant…" value="${esc(state.filters.q)}" oninput="debounceFilter()">
      <span style="margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--muted)">${state.total} merchants</span>
    </div>
    <div class="table-wrap">
      <table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>
    </div>
    <div class="pagination">
      <button onclick="goPage(1)" ${state.page===1?'disabled':''}>«</button>
      <button onclick="goPage(${state.page-1})" ${state.page===1?'disabled':''}>‹</button>
      <span class="page-info">${start}–${end} of ${state.total}</span>
      <button onclick="goPage(${state.page+1})" ${state.page>=totalPages?'disabled':''}>›</button>
      <button onclick="goPage(${totalPages})" ${state.page>=totalPages?'disabled':''}>»</button>
    </div>
  `;
}

// ════════════════════════════════════════════════════════════════
// CATEGORIES
// ════════════════════════════════════════════════════════════════
async function renderCategories() {
  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="empty loading">loading…</div>';

  const res = await api('categories');

  const tbody = res.rows.length === 0
    ? `<tr><td colspan="3" class="empty">no categories</td></tr>`
    : res.rows.map(row => `<tr>
        <td><span class="pill cat">${esc(row.name)}</span></td>
        <td style="color:var(--muted)">${row.merchant_count} merchants</td>
        <td style="color:var(--muted)">${row.tx_count} transactions</td>
      </tr>`).join('');

  main.innerHTML = `
    <div class="toolbar">
      <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${res.rows.length} categories</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Category</th><th>Merchants</th><th>Transactions</th>
        </tr></thead>
        <tbody>${tbody}</tbody>
      </table>
    </div>
  `;
}

// ── helpers ───────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function sortBy(col) {
  if (state.sortCol === col) state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
  else { state.sortCol = col; state.sortDir = 'asc'; }
  state.page = 1;
  renderTab();
}

function goPage(p) {
  state.page = p;
  renderTab();
}

let _debTimer;
function debounceFilter() { clearTimeout(_debTimer); _debTimer = setTimeout(applyFilters, 280); }

function applyFilters() {
  state.filters.q          = document.getElementById('q')?.value ?? '';
  state.filters.category   = document.getElementById('filter-cat')?.value ?? '';
  state.filters.account    = document.getElementById('filter-acc')?.value ?? '';
  state.filters.amountMin  = document.getElementById('amt-min')?.value ?? '';
  state.filters.amountMax  = document.getElementById('amt-max')?.value ?? '';
  state.page = 1;
  renderTab();
}

function clearFilters() {
  state.filters = { q: '', category: '', account: '', amountMin: '', amountMax: '' };
  state.page = 1;
  renderTab();
}

// ── boot ──────────────────────────────────────────────────────────────────
renderTab();
</script>
</body>
</html>
"""

# ── DB queries ────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def q_meta():
    with get_conn() as conn:
        cats = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        accs = conn.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
    return {
        "db_path": str(DB_PATH),
        "categories": [{"id": r["id"], "name": r["name"]} for r in cats],
        "accounts":   [{"id": r["id"], "name": r["name"]} for r in accs],
    }


def q_transactions(params):
    page      = max(1, int(params.get("page", [1])[0]))
    page_size = min(500, max(1, int(params.get("page_size", [50])[0])))
    sort_map  = {"date": "t.date", "description": "t.description", "merchant": "m.name",
                 "category": "c.name", "account": "a.name", "amount": "t.amount"}
    sort_col  = sort_map.get(params.get("sort", ["date"])[0], "t.date")
    sort_dir  = "ASC" if params.get("dir", ["desc"])[0].lower() == "asc" else "DESC"
    q         = (params.get("q", [""])[0] or "").strip()
    cat_id    = params.get("category", [""])[0]
    acc_id    = params.get("account",  [""])[0]
    amt_min   = params.get("amount_min", [""])[0]
    amt_max   = params.get("amount_max", [""])[0]

    where, args = [], []
    if q:
        where.append("(t.description LIKE ? OR m.name LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if cat_id:
        where.append("t.category_id = ?"); args.append(cat_id)
    if acc_id:
        where.append("t.account_id = ?"); args.append(acc_id)
    if amt_min:
        where.append("ABS(t.amount) >= ?"); args.append(float(amt_min))
    if amt_max:
        where.append("ABS(t.amount) <= ?"); args.append(float(amt_max))

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    base = f"""
        FROM transactions t
        LEFT JOIN merchants m ON m.id = t.merchant_id
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN accounts a ON a.id = t.account_id
        {clause}
    """
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
        rows  = conn.execute(
            f"SELECT t.date, t.description, t.amount, m.name as merchant, c.name as category, a.name as account {base} ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size]
        ).fetchall()
    return {
        "total": total,
        "rows": [dict(r) for r in rows],
    }


def q_merchants(params):
    page      = max(1, int(params.get("page", [1])[0]))
    page_size = min(500, max(1, int(params.get("page_size", [50])[0])))
    sort_map  = {"name": "m.name", "category": "c.name", "alias_count": "alias_count",
                 "tx_count": "tx_count", "created_at": "m.created_at"}
    sort_col  = sort_map.get(params.get("sort", ["name"])[0], "m.name")
    sort_dir  = "ASC" if params.get("dir", ["asc"])[0].lower() == "asc" else "DESC"
    q         = (params.get("q", [""])[0] or "").strip()

    where, args = [], []
    if q:
        where.append("m.name LIKE ?"); args.append(f"%{q}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT m.name, c.name as category, m.created_at,
               COUNT(DISTINCT ma.id) as alias_count,
               COUNT(DISTINCT t.id)  as tx_count
        FROM merchants m
        LEFT JOIN categories c ON c.id = m.category_id
        LEFT JOIN merchant_aliases ma ON ma.merchant_id = m.id
        LEFT JOIN transactions t ON t.merchant_id = m.id
        {clause}
        GROUP BY m.id
        ORDER BY {sort_col} {sort_dir}
        LIMIT ? OFFSET ?
    """
    count_sql = f"SELECT COUNT(*) FROM merchants m {clause}"
    with get_conn() as conn:
        total = conn.execute(count_sql, args).fetchone()[0]
        rows  = conn.execute(sql, args + [page_size, (page - 1) * page_size]).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows]}


def q_categories():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.name,
                   COUNT(DISTINCT m.id)  as merchant_count,
                   COUNT(DISTINCT t.id)  as tx_count
            FROM categories c
            LEFT JOIN merchants m ON m.category_id = c.id
            LEFT JOIN transactions t ON t.category_id = c.id
            GROUP BY c.id
            ORDER BY tx_count DESC, c.name
        """).fetchall()
    return {"rows": [dict(r) for r in rows]}


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence request logs

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path   = parsed.path

        try:
            if path == "/" or path == "":
                self.send_html(HTML)
            elif path == "/api/meta":
                self.send_json(q_meta())
            elif path == "/api/transactions":
                self.send_json(q_transactions(params))
            elif path == "/api/merchants":
                self.send_json(q_merchants(params))
            elif path == "/api/categories":
                self.send_json(q_categories())
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Budget DB browser UI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[!] DB not found at {DB_PATH}. Run ingest.py first.")
        sys.exit(1)

    url = f"http://localhost:{args.port}"
    print(f"[budget-db] Serving at {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)

    server = HTTPServer(("", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[budget-db] Stopped.")


if __name__ == "__main__":
    main()