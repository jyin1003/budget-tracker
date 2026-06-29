"""
db/db_viewer.py — Lightweight browser UI for budget.db with full inline CRUD.

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

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Budget DB Viewer</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:      #0d0f12;
    --surface: #13161b;
    --border:  #1e2228;
    --border2: #2a2f38;
    --text:    #c8d0dc;
    --muted:   #5a6275;
    --accent:  #4fffb0;
    --red:     #ff6b6b;
    --yellow:  #ffd166;
    --mono:    'IBM Plex Mono', monospace;
    --sans:    'IBM Plex Sans', sans-serif;
  }

  html, body { height: 100%; background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 14px; }
  #app { display: grid; grid-template-rows: auto 1fr; height: 100vh; }

  /* header */
  header {
    border-bottom: 1px solid var(--border); padding: 0 24px;
    display: flex; align-items: center; gap: 32px; height: 52px;
    background: var(--surface);
  }
  .logo { font-family: var(--mono); font-weight: 600; font-size: 13px; color: var(--accent); letter-spacing: .08em; }
  .logo span { color: var(--muted); }
  nav { display: flex; gap: 2px; }
  nav button {
    background: none; border: none; cursor: pointer; font-family: var(--mono);
    font-size: 12px; font-weight: 500; color: var(--muted); padding: 6px 14px;
    border-radius: 4px; letter-spacing: .04em; transition: color .15s, background .15s;
  }
  nav button:hover { color: var(--text); background: var(--border); }
  nav button.active { color: var(--accent); background: rgba(79,255,176,.08); }
  #status-bar { margin-left: auto; font-family: var(--mono); font-size: 11px; color: var(--muted); }
  #status-bar .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); margin-right: 6px; animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

  main { overflow: hidden; display: flex; flex-direction: column; }

  /* toolbar */
  .toolbar {
    padding: 12px 24px; border-bottom: 1px solid var(--border);
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
  }
  .toolbar input, .toolbar select {
    background: var(--bg); border: 1px solid var(--border2); color: var(--text);
    font-family: var(--mono); font-size: 12px; padding: 6px 10px; border-radius: 4px;
    outline: none; transition: border-color .15s;
  }
  .toolbar input:focus, .toolbar select:focus { border-color: var(--accent); }
  .toolbar input { width: 220px; }
  .toolbar select { min-width: 140px; }
  .toolbar label { font-family: var(--mono); font-size: 11px; color: var(--muted); }

  /* buttons */
  .btn {
    background: rgba(79,255,176,.1); border: 1px solid rgba(79,255,176,.25);
    color: var(--accent); font-family: var(--mono); font-size: 12px;
    padding: 6px 14px; border-radius: 4px; cursor: pointer; transition: background .15s;
  }
  .btn:hover { background: rgba(79,255,176,.2); }
  .btn.ghost { background: none; border-color: var(--border2); color: var(--muted); }
  .btn.ghost:hover { border-color: var(--text); color: var(--text); }
  .btn.danger { background: rgba(255,107,107,.08); border-color: rgba(255,107,107,.3); color: var(--red); }
  .btn.danger:hover { background: rgba(255,107,107,.18); }

  /* table */
  .table-wrap { flex: 1; overflow: auto; padding: 0 24px 16px; }
  table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }
  thead th {
    position: sticky; top: 0; background: var(--bg); text-align: left;
    padding: 10px 8px 10px 0; font-weight: 500; font-size: 11px;
    letter-spacing: .07em; text-transform: uppercase; color: var(--muted);
    border-bottom: 1px solid var(--border); white-space: nowrap;
    cursor: pointer; user-select: none;
  }
  thead th:hover { color: var(--text); }
  thead th.sorted { color: var(--accent); }
  thead th .arrow { margin-left: 3px; opacity: .5; }
  thead th.sorted .arrow { opacity: 1; }
  thead th.no-sort { cursor: default; }
  thead th.no-sort:hover { color: var(--muted); }

  tbody tr { transition: background .08s; }
  tbody tr:hover { background: rgba(255,255,255,.025); }
  tbody td {
    padding: 7px 8px 7px 0; border-bottom: 1px solid var(--border);
    vertical-align: middle; max-width: 280px;
  }

  /* editable cells */
  td.editable { cursor: pointer; }
  td.editable:hover { background: rgba(79,255,176,.05); }
  td.editable:hover::after { content: ' ✎'; font-size: 9px; color: var(--muted); }
  td.editing { padding: 2px 0; cursor: default; }
  td.editing:hover { background: none; }
  td.editing:hover::after { content: none; }
  td.editing input, td.editing select {
    width: 100%; background: var(--surface); border: 1px solid var(--accent);
    color: var(--text); font-family: var(--mono); font-size: 12px;
    padding: 4px 7px; border-radius: 3px; outline: none;
  }

  /* pills */
  .pill {
    display: inline-block; padding: 2px 8px; border-radius: 20px;
    font-size: 10px; font-weight: 500; letter-spacing: .04em;
    background: rgba(79,255,176,.1); color: var(--accent); border: 1px solid rgba(79,255,176,.2);
    white-space: nowrap;
  }
  .pill.cat  { background: rgba(255,209,102,.08); color: var(--yellow); border-color: rgba(255,209,102,.2); }
  .pill.none { background: rgba(90,98,117,.12); color: var(--muted); border-color: var(--border2); }
  .pill.alias { background: rgba(90,98,117,.18); color: var(--text); border-color: var(--border2); font-size: 10px; max-width: 380px; overflow: hidden; text-overflow: ellipsis; }

  /* delete button inline */
  .del-btn {
    background: none; border: none; color: var(--muted); cursor: pointer;
    font-size: 13px; padding: 2px 4px; border-radius: 3px; line-height: 1;
    transition: color .12s, background .12s; opacity: 0;
  }
  tr:hover .del-btn { opacity: 1; }
  .del-btn:hover { color: var(--red); background: rgba(255,107,107,.1); }

  /* expand rows */
  .expand-btn {
    background: none; border: 1px solid var(--border2); color: var(--muted);
    font-family: var(--mono); font-size: 10px; padding: 1px 7px; border-radius: 3px;
    cursor: pointer; transition: border-color .12s, color .12s;
  }
  .expand-btn:hover, .expand-btn.open { border-color: var(--accent); color: var(--accent); background: rgba(79,255,176,.07); }
  .detail-row td { background: rgba(79,255,176,.02); padding: 10px 8px 10px 20px; border-bottom: 1px solid var(--border); }
  .detail-row .alias-list, .detail-row .merchant-list { display: flex; flex-wrap: wrap; gap: 6px; }

  /* amount */
  .neg { color: var(--red); }
  .pos { color: var(--accent); }

  /* pagination */
  .pagination {
    padding: 10px 24px; border-top: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px;
    font-family: var(--mono); font-size: 11px; color: var(--muted);
    background: var(--surface);
  }
  .pagination button {
    background: none; border: 1px solid var(--border2); color: var(--muted);
    font-family: var(--mono); font-size: 11px; padding: 3px 10px;
    border-radius: 3px; cursor: pointer;
  }
  .pagination button:hover:not(:disabled) { border-color: var(--text); color: var(--text); }
  .pagination button:disabled { opacity: .3; cursor: default; }

  /* toast */
  #toast {
    position: fixed; bottom: 24px; right: 24px; z-index: 999;
    font-family: var(--mono); font-size: 12px; padding: 10px 16px;
    border-radius: 6px; background: var(--surface); border: 1px solid var(--border2);
    color: var(--text); opacity: 0; transition: opacity .2s; pointer-events: none;
  }
  #toast.show { opacity: 1; }
  #toast.ok  { border-color: var(--accent); color: var(--accent); }
  #toast.err { border-color: var(--red);    color: var(--red); }

  /* new-row */
  .new-row td { background: rgba(79,255,176,.04); }
  .new-row input, .new-row select {
    background: var(--bg); border: 1px solid var(--border2); color: var(--text);
    font-family: var(--mono); font-size: 12px; padding: 4px 7px;
    border-radius: 3px; outline: none; width: 100%;
  }
  .new-row input:focus, .new-row select:focus { border-color: var(--accent); }

  /* misc */
  .empty { padding: 60px; text-align: center; color: var(--muted); font-family: var(--mono); font-size: 12px; }
  .loading { opacity: .5; }
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
</style>
</head>
<body>
<div id="app">
  <header>
    <div class="logo">budget<span>.</span>database<span>.</span>viewer</div>
    <nav>
      <button class="active" onclick="switchTab('transactions')">transactions</button>
      <button onclick="switchTab('merchants')">merchants</button>
      <button onclick="switchTab('categories')">categories</button>
    </nav>
    <div id="status-bar"><span class="dot"></span><span id="db-path"></span></div>
  </header>
  <main id="main-content"></main>
</div>
<div id="toast"></div>

<script>
// ══════════════════════════════════════════════════════════════════
// STATE & UTILS
// ══════════════════════════════════════════════════════════════════
const S = {
  tab: 'transactions', page: 1, pageSize: 50, total: 0,
  sortCol: 'date', sortDir: 'desc',
  filters: { q:'', category:'', account:'', amountMin:'', amountMax:'' },
  meta: { categories:[], accounts:[] },
};

// Global flag: prevents renderTab() from firing while a cell is being edited.
// Set to true when an edit begins, false when it commits or cancels.
let _editInProgress = false;

function esc(s) {
  return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

let _toast;
function toast(msg, type='ok') {
  clearTimeout(_toast);
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${type}`;
  _toast = setTimeout(() => el.className = '', 2500);
}

async function apiGet(ep, params={}) {
  const qs = new URLSearchParams(params).toString();
  const r = await fetch(`/api/${ep}${qs?'?'+qs:''}`);
  return r.json();
}

async function apiPost(ep, body) {
  const r = await fetch(`/api/${ep}`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  return r.json();
}

// ══════════════════════════════════════════════════════════════════
// TAB SWITCHING
// ══════════════════════════════════════════════════════════════════
function switchTab(tab) {
  if (_editInProgress) return; // don't switch mid-edit
  S.tab = tab; S.page = 1;
  S.sortCol = tab==='transactions' ? 'date' : 'name';
  S.sortDir = tab==='transactions' ? 'desc' : 'asc';
  S.filters = { q:'', category:'', account:'', amountMin:'', amountMax:'' };
  document.querySelectorAll('nav button').forEach((b,i) =>
    b.classList.toggle('active', ['transactions','merchants','categories'][i]===tab));
  renderTab();
}

function renderTab() {
  if (_editInProgress) return; // never re-render while editing
  if (S.tab==='transactions') renderTransactions();
  else if (S.tab==='merchants') renderMerchants();
  else renderCategories();
}

function sortBy(col) {
  if (_editInProgress) return;
  if (S.sortCol===col) S.sortDir = S.sortDir==='asc'?'desc':'asc';
  else { S.sortCol=col; S.sortDir='asc'; }
  S.page=1; renderTab();
}
function goPage(p) {
  if (_editInProgress) return;
  S.page=p; renderTab();
}

let _deb;
function debounceFilter() { clearTimeout(_deb); _deb=setTimeout(applyFilters,280); }
function applyFilters() {
  if (_editInProgress) return;
  S.filters.q         = document.getElementById('q')?.value ?? '';
  S.filters.category  = document.getElementById('filter-cat')?.value ?? '';
  S.filters.account   = document.getElementById('filter-acc')?.value ?? '';
  S.filters.amountMin = document.getElementById('amt-min')?.value ?? '';
  S.filters.amountMax = document.getElementById('amt-max')?.value ?? '';
  S.page=1; renderTab();
}
function clearFilters() {
  if (_editInProgress) return;
  S.filters={q:'',category:'',account:'',amountMin:'',amountMax:''};
  S.page=1; renderTab();
}

function thHtml(cols) {
  return cols.map(c => {
    if (c.noSort) return `<th class="no-sort">${c.label}</th>`;
    const sorted = S.sortCol===c.key;
    const arrow = sorted ? (S.sortDir==='asc'?'↑':'↓') : '↕';
    return `<th class="${sorted?'sorted':''}" onclick="sortBy('${c.key}')">${c.label} <span class="arrow">${arrow}</span></th>`;
  }).join('');
}

function paginationHtml(totalPages, start, end) {
  return `
    <div class="pagination">
      <button onclick="goPage(1)" ${S.page===1?'disabled':''}>«</button>
      <button onclick="goPage(${S.page-1})" ${S.page===1?'disabled':''}>‹</button>
      <span style="margin:0 4px">${start}–${end} of ${S.total.toLocaleString()}</span>
      <button onclick="goPage(${S.page+1})" ${S.page>=totalPages?'disabled':''}>›</button>
      <button onclick="goPage(${totalPages})" ${S.page>=totalPages?'disabled':''}>»</button>
      <select onchange="S.pageSize=+this.value;S.page=1;renderTab()" style="margin-left:8px">
        ${[25,50,100,250].map(n=>`<option value="${n}" ${S.pageSize===n?'selected':''}>${n}/page</option>`).join('')}
      </select>
    </div>`;
}

// ══════════════════════════════════════════════════════════════════
// INLINE EDIT HELPERS
// ══════════════════════════════════════════════════════════════════

/**
 * makeEditableText — replaces a td's content with a text input.
 *
 * Commit triggers: Enter key, or clicking anywhere outside the td.
 * Cancel trigger:  Escape key.
 *
 * Uses a mousedown listener on document to detect outside clicks
 * BEFORE blur fires, so we can distinguish "clicked away to save"
 * from "blur fired because of something internal".
 *
 * renderTab() is NEVER called from inside these helpers — the caller
 * decides when to re-render by setting _editInProgress = false first.
 */
function makeEditableText(td, value, onSave) {
  _editInProgress = true;
  td.classList.remove('editable');
  td.classList.add('editing');
  td.innerHTML = '';

  const inp = document.createElement('input');
  inp.type = 'text';
  inp.value = value ?? '';
  td.appendChild(inp);
  inp.focus();
  inp.select();

  let committed = false;

  const commit = async (save) => {
    if (committed) return;
    committed = true;
    document.removeEventListener('mousedown', outsideClick, true);

    const newVal = inp.value.trim();
    const changed = newVal !== (value ?? '').trim();

    _editInProgress = false;

    if (save && changed) {
      const res = await onSave(newVal);
      if (res?.error) {
        toast('Error: ' + res.error, 'err');
        renderTab();
        return;
      }
      toast('Saved ✓');
    }
    renderTab();
  };

  const outsideClick = (e) => {
    if (!td.contains(e.target)) {
      commit(true);
    }
  };

  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter')  { e.preventDefault(); commit(true);  }
    if (e.key === 'Escape') { e.preventDefault(); commit(false); }
  });

  // Add the outside-click listener with a small delay so the click
  // that opened this editor doesn't immediately close it.
  setTimeout(() => {
    document.addEventListener('mousedown', outsideClick, true);
  }, 0);
}

/**
 * makeEditableSelect — replaces a td's content with a <select>.
 *
 * Commit triggers: selecting an option (change event), or clicking outside.
 * Cancel trigger:  Escape key.
 *
 * The old approach used blur + setTimeout(120) which raced against renderTab.
 * Now we use mousedown-outside detection, same pattern as makeEditableText.
 */
function makeEditableSelect(td, options, currentValue, onSave) {
  _editInProgress = true;
  td.classList.remove('editable');
  td.classList.add('editing');
  td.innerHTML = '';

  const sel = document.createElement('select');
  sel.innerHTML = `<option value="">— none —</option>` +
    options.map(o =>
      `<option value="${o.id}" ${(o.name === currentValue || String(o.id) === String(currentValue)) ? 'selected' : ''}>${esc(o.name)}</option>`
    ).join('');
  td.appendChild(sel);
  sel.focus();

  let committed = false;

  const commit = async (save) => {
    if (committed) return;
    committed = true;
    document.removeEventListener('mousedown', outsideClick, true);

    const chosen = options.find(o => String(o.id) === sel.value);
    const changed = (chosen?.name ?? '') !== (currentValue ?? '');

    _editInProgress = false;

    if (save && changed) {
      const res = await onSave(sel.value, chosen?.name);
      if (res?.error) {
        toast('Error: ' + res.error, 'err');
        renderTab();
        return;
      }
      toast('Saved ✓');
    }
    renderTab();
  };

  const outsideClick = (e) => {
    if (!td.contains(e.target)) {
      commit(true);
    }
  };

  sel.addEventListener('change', () => commit(true));
  sel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); commit(false); }
  });

  setTimeout(() => {
    document.addEventListener('mousedown', outsideClick, true);
  }, 0);
}

// ══════════════════════════════════════════════════════════════════
// TRANSACTIONS
// ══════════════════════════════════════════════════════════════════
async function renderTransactions() {
  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="empty loading">loading…</div>';

  const [meta, res] = await Promise.all([
    apiGet('meta'),
    apiGet('transactions', {
      page:S.page, page_size:S.pageSize, sort:S.sortCol, dir:S.sortDir,
      q:S.filters.q, category:S.filters.category, account:S.filters.account,
      amount_min:S.filters.amountMin, amount_max:S.filters.amountMax,
    })
  ]);
  S.meta = meta;
  document.getElementById('db-path').textContent = meta.db_path;
  S.total = res.total;

  const catOpts = meta.categories.map(c=>`<option value="${c.id}">${c.name}</option>`).join('');
  const accOpts = meta.accounts.map(a=>`<option value="${a.id}">${a.name}</option>`).join('');

  const cols = [
    {key:'date',        label:'Date'},
    {key:'description', label:'Description'},
    {key:'merchant',    label:'Merchant'},
    {key:'category',    label:'Category'},
    {key:'account',     label:'Account'},
    {key:'amount',      label:'Amount'},
    {key:'notes',       label:'Notes'},
    {noSort:true,       label:''},
  ];

  const totalPages = Math.ceil(S.total/S.pageSize)||1;
  const start = (S.page-1)*S.pageSize+1;
  const end   = Math.min(S.page*S.pageSize, S.total);

  main.innerHTML = `
    <div class="toolbar">
      <input id="q" type="search" placeholder="search description / merchant…" value="${esc(S.filters.q)}" oninput="debounceFilter()">
      <select id="filter-cat" onchange="applyFilters()">
        <option value="">all categories</option>${catOpts}
      </select>
      <select id="filter-acc" onchange="applyFilters()">
        <option value="">all accounts</option>${accOpts}
      </select>
      <label>min $<input id="amt-min" type="number" style="width:76px" placeholder="0" value="${S.filters.amountMin}" oninput="debounceFilter()"></label>
      <label>max $<input id="amt-max" type="number" style="width:76px" placeholder="∞" value="${S.filters.amountMax}" oninput="debounceFilter()"></label>
      <button class="btn ghost" onclick="clearFilters()">clear</button>
      <button class="btn" onclick="addTransactionRow()" style="margin-left:auto">+ add row</button>
      <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${S.total.toLocaleString()} rows</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>${thHtml(cols)}</tr></thead>
        <tbody id="tx-tbody"></tbody>
      </table>
    </div>
    ${paginationHtml(totalPages, start, end)}
  `;

  if (S.filters.category) document.getElementById('filter-cat').value = S.filters.category;
  if (S.filters.account)  document.getElementById('filter-acc').value = S.filters.account;

  const tbody = document.getElementById('tx-tbody');
  if (!res.rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">no transactions match</td></tr>`;
    return;
  }

  res.rows.forEach(row => {
    const tr = document.createElement('tr');
    const amt = row.amount ?? 0;
    const amtStr = (amt<0?'-':'+') + '$' + Math.abs(amt).toFixed(2);

    tr.innerHTML = `
      <td class="editable" data-field="date">${esc(row.date)}</td>
      <td class="editable" data-field="description" title="${esc(row.description)}">${esc((row.description||'').substring(0,50))}${(row.description||'').length>50?'…':''}</td>
      <td class="editable" data-field="merchant">${row.merchant?`<span class="pill">${esc(row.merchant)}</span>`:`<span class="pill none">—</span>`}</td>
      <td class="editable" data-field="category">${row.category?`<span class="pill cat">${esc(row.category)}</span>`:`<span class="pill none">—</span>`}</td>
      <td class="editable" data-field="account">${esc(row.account)}</td>
      <td class="editable ${amt<0?'neg':'pos'}" data-field="amount" style="text-align:right">${amtStr}</td>
      <td class="editable" data-field="notes" style="color:var(--muted)">${esc(row.notes||'')}</td>
      <td style="width:28px"><button class="del-btn" title="delete">✕</button></td>
    `;

    // delete
    tr.querySelector('.del-btn').addEventListener('click', async () => {
      if (_editInProgress) return;
      if (!confirm(`Delete transaction: ${row.date} ${row.description}?`)) return;
      const res2 = await apiPost('delete', {table:'transactions', id:row.id});
      if (res2.error) { toast('Error: '+res2.error,'err'); return; }
      toast('Deleted'); renderTab();
    });

    // editable cells
    tr.querySelectorAll('td.editable').forEach(td => {
      td.addEventListener('click', () => {
        if (_editInProgress) return; // ignore clicks while another edit is open
        const field = td.dataset.field;
        if (field==='merchant') {
          const opts = S.meta.merchants || [];
          makeEditableSelect(td, opts, row.merchant, async (id) => {
            return apiPost('update', {table:'transactions', id:row.id, field:'merchant_id', value:id||null});
          });
        } else if (field==='category') {
          makeEditableSelect(td, S.meta.categories, row.category, async (id) => {
            return apiPost('update', {table:'transactions', id:row.id, field:'category_id', value:id||null});
          });
        } else if (field==='account') {
          makeEditableSelect(td, S.meta.accounts, row.account, async (id) => {
            return apiPost('update', {table:'transactions', id:row.id, field:'account_id', value:id||null});
          });
        } else if (field==='amount') {
          makeEditableText(td, String(amt), async (val) => {
            const n = parseFloat(val);
            if (isNaN(n)) return {error:'invalid number'};
            return apiPost('update', {table:'transactions', id:row.id, field:'amount', value:n});
          });
        } else {
          makeEditableText(td, row[field]??'', async (val) => {
            return apiPost('update', {table:'transactions', id:row.id, field, value:val});
          });
        }
      });
    });

    tbody.appendChild(tr);
  });
}

async function addTransactionRow() {
  if (_editInProgress) return;
  const meta = S.meta;
  const tbody = document.getElementById('tx-tbody');
  if (!tbody) return;

  tbody.querySelector('.new-row')?.remove();

  const catOpts = meta.categories.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const accOpts = meta.accounts.map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join('');
  const mOpts   = (meta.merchants||[]).map(m=>`<option value="${m.id}">${esc(m.name)}</option>`).join('');

  const today = new Date().toISOString().slice(0,10);
  const tr = document.createElement('tr');
  tr.className = 'new-row';
  tr.innerHTML = `
    <td><input id="nr-date" type="date" value="${today}"></td>
    <td><input id="nr-desc" type="text" placeholder="description"></td>
    <td><select id="nr-merchant"><option value="">— none —</option>${mOpts}</select></td>
    <td><select id="nr-cat"><option value="">— none —</option>${catOpts}</select></td>
    <td><select id="nr-acc"><option value="">— none —</option>${accOpts}</select></td>
    <td><input id="nr-amt" type="number" step="0.01" placeholder="0.00" style="width:90px"></td>
    <td><input id="nr-notes" type="text" placeholder="notes (optional)"></td>
    <td>
      <button class="btn" style="padding:3px 9px;font-size:11px" id="nr-save">✓</button>
      <button class="btn ghost" style="padding:3px 7px;font-size:11px;margin-left:3px" onclick="this.closest('tr').remove()">✕</button>
    </td>
  `;
  tbody.insertBefore(tr, tbody.firstChild);
  document.getElementById('nr-desc').focus();

  document.getElementById('nr-save').addEventListener('click', async () => {
    const date    = document.getElementById('nr-date').value;
    const desc    = document.getElementById('nr-desc').value.trim();
    const merchant= document.getElementById('nr-merchant').value;
    const cat     = document.getElementById('nr-cat').value;
    const acc     = document.getElementById('nr-acc').value;
    const amt     = parseFloat(document.getElementById('nr-amt').value);
    const notes   = document.getElementById('nr-notes').value.trim();
    if (!date||!desc||!acc||isNaN(amt)) { toast('Date, description, account and amount are required','err'); return; }
    const res = await apiPost('insert', {table:'transactions', data:{date,description:desc,merchant_id:merchant||null,category_id:cat||null,account_id:acc,amount:amt,notes:notes||null}});
    if (res.error) { toast('Error: '+res.error,'err'); return; }
    toast('Added ✓'); renderTab();
  });
}

// ══════════════════════════════════════════════════════════════════
// MERCHANTS
// ══════════════════════════════════════════════════════════════════
async function renderMerchants() {
  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="empty loading">loading…</div>';

  const [meta, res] = await Promise.all([
    apiGet('meta'),
    apiGet('merchants', {page:S.page, page_size:S.pageSize, sort:S.sortCol, dir:S.sortDir, q:S.filters.q})
  ]);
  S.meta = meta;
  S.total = res.total;

  const cols = [
    {key:'name',        label:'Merchant'},
    {key:'category',    label:'Category'},
    {key:'alias_count', label:'Aliases'},
    {key:'tx_count',    label:'Transactions'},
    {key:'created_at',  label:'Added'},
    {noSort:true,       label:''},
  ];

  const totalPages = Math.ceil(S.total/S.pageSize)||1;
  const start = (S.page-1)*S.pageSize+1;
  const end   = Math.min(S.page*S.pageSize, S.total);

  main.innerHTML = `
    <div class="toolbar">
      <input id="q" type="search" placeholder="search merchant…" value="${esc(S.filters.q)}" oninput="debounceFilter()">
      <button class="btn" onclick="addMerchantRow()" style="margin-left:auto">+ add merchant</button>
      <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${S.total} merchants</span>
    </div>
    <div class="table-wrap">
      <table><thead><tr>${thHtml(cols)}</tr></thead><tbody id="m-tbody"></tbody></table>
    </div>
    ${paginationHtml(totalPages, start, end)}
  `;

  const tbody = document.getElementById('m-tbody');
  if (!res.rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">no merchants yet</td></tr>`;
    return;
  }

  res.rows.forEach((row, i) => {
    const mainTr = document.createElement('tr');
    const hasAliases = row.alias_count > 0;
    mainTr.innerHTML = `
      <td class="editable" data-field="name"><strong>${esc(row.name)}</strong></td>
      <td class="editable" data-field="category"><span class="pill cat">${esc(row.category)}</span></td>
      <td>${hasAliases ? `<button class="expand-btn" id="mbtn-${i}">${row.alias_count} alias${row.alias_count!==1?'es':''} ▾</button>` : `<span style="color:var(--muted);font-size:11px">0 aliases</span>`}</td>
      <td style="color:var(--muted)">${row.tx_count}</td>
      <td style="color:var(--muted)">${(row.created_at||'').slice(0,10)}</td>
      <td style="width:28px"><button class="del-btn" title="delete">✕</button></td>
    `;

    mainTr.querySelector('[data-field="name"]').addEventListener('click', function() {
      if (_editInProgress) return;
      makeEditableText(this, row.name, async (val) => {
        return apiPost('update', {table:'merchants', id:row.id, field:'name', value:val});
      });
    });

    mainTr.querySelector('[data-field="category"]').addEventListener('click', function() {
      if (_editInProgress) return;
      makeEditableSelect(this, S.meta.categories, row.category, async (id) => {
        return apiPost('update', {table:'merchants', id:row.id, field:'category_id', value:id||null});
      });
    });

    mainTr.querySelector('.del-btn').addEventListener('click', async () => {
      if (_editInProgress) return;
      if (!confirm(`Delete merchant "${row.name}"? This will unlink its transactions.`)) return;
      const r = await apiPost('delete', {table:'merchants', id:row.id});
      if (r.error) { toast('Error: '+r.error,'err'); return; }
      toast('Deleted'); renderTab();
    });

    tbody.appendChild(mainTr);

    const detailTr = document.createElement('tr');
    detailTr.className = 'detail-row';
    detailTr.id = `mdetail-${i}`;
    detailTr.style.display = 'none';
    detailTr.innerHTML = `<td colspan="6"><div class="alias-list" id="maliases-${i}"><span style="color:var(--muted);font-size:11px">loading…</span></div></td>`;
    tbody.appendChild(detailTr);

    if (hasAliases) {
      document.getElementById(`mbtn-${i}`).addEventListener('click', () => toggleExpand(`mdetail-${i}`, `mbtn-${i}`, async () => {
        const r = await apiGet('aliases', {merchant: row.name});
        document.getElementById(`maliases-${i}`).innerHTML = (r.aliases||[]).length
          ? r.aliases.map(a=>`<span class="pill alias" title="${esc(a)}">${esc(a)}</span>`).join('')
          : '<span style="color:var(--muted);font-size:11px">no aliases</span>';
      }));
    }
  });
}

async function addMerchantRow() {
  if (_editInProgress) return;
  const tbody = document.getElementById('m-tbody');
  if (!tbody) return;
  tbody.querySelector('.new-row')?.remove();
  const catOpts = S.meta.categories.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const tr = document.createElement('tr');
  tr.className = 'new-row';
  tr.innerHTML = `
    <td><input id="nm-name" type="text" placeholder="merchant name"></td>
    <td><select id="nm-cat"><option value="">— none —</option>${catOpts}</select></td>
    <td colspan="3"></td>
    <td>
      <button class="btn" style="padding:3px 9px;font-size:11px" id="nm-save">✓</button>
      <button class="btn ghost" style="padding:3px 7px;font-size:11px;margin-left:3px" onclick="this.closest('tr').remove()">✕</button>
    </td>
  `;
  tbody.insertBefore(tr, tbody.firstChild);
  document.getElementById('nm-name').focus();
  document.getElementById('nm-save').addEventListener('click', async () => {
    const name = document.getElementById('nm-name').value.trim();
    const cat  = document.getElementById('nm-cat').value;
    if (!name) { toast('Merchant name required','err'); return; }
    const r = await apiPost('insert', {table:'merchants', data:{name, category_id:cat||null}});
    if (r.error) { toast('Error: '+r.error,'err'); return; }
    toast('Added ✓'); renderTab();
  });
}

// ══════════════════════════════════════════════════════════════════
// CATEGORIES
// ══════════════════════════════════════════════════════════════════
async function renderCategories() {
  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="empty loading">loading…</div>';

  const [meta, res] = await Promise.all([apiGet('meta'), apiGet('categories')]);
  S.meta = meta;

  main.innerHTML = `
    <div class="toolbar">
      <span style="font-family:var(--mono);font-size:11px;color:var(--muted)">${res.rows.length} categories</span>
      <button class="btn" onclick="addCategoryRow()" style="margin-left:auto">+ add category</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Category</th>
          <th class="no-sort">Merchants</th>
          <th class="no-sort">Transactions</th>
          <th class="no-sort"></th>
        </tr></thead>
        <tbody id="cat-tbody"></tbody>
      </table>
    </div>
  `;

  const tbody = document.getElementById('cat-tbody');
  if (!res.rows.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty">no categories</td></tr>`;
    return;
  }

  res.rows.forEach((row, i) => {
    const mainTr = document.createElement('tr');
    const hasMerchants = row.merchant_count > 0;
    mainTr.innerHTML = `
      <td class="editable" data-field="name"><span class="pill cat">${esc(row.name)}</span></td>
      <td>${hasMerchants ? `<button class="expand-btn" id="cbtn-${i}">${row.merchant_count} merchant${row.merchant_count!==1?'s':''} ▾</button>` : `<span style="color:var(--muted);font-size:11px">0 merchants</span>`}</td>
      <td style="color:var(--muted)">${row.tx_count} transactions</td>
      <td style="width:28px"><button class="del-btn" title="delete">✕</button></td>
    `;

    mainTr.querySelector('[data-field="name"]').addEventListener('click', function() {
      if (_editInProgress) return;
      makeEditableText(this, row.name, async (val) => {
        return apiPost('update', {table:'categories', id:row.id, field:'name', value:val});
      });
    });

    mainTr.querySelector('.del-btn').addEventListener('click', async () => {
      if (_editInProgress) return;
      if (!confirm(`Delete category "${row.name}"? Merchants and transactions will be unlinked.`)) return;
      const r = await apiPost('delete', {table:'categories', id:row.id});
      if (r.error) { toast('Error: '+r.error,'err'); return; }
      toast('Deleted'); renderTab();
    });

    tbody.appendChild(mainTr);

    const detailTr = document.createElement('tr');
    detailTr.className = 'detail-row';
    detailTr.id = `cdetail-${i}`;
    detailTr.style.display = 'none';
    detailTr.innerHTML = `<td colspan="4"><div class="merchant-list" id="cmerchants-${i}"><span style="color:var(--muted);font-size:11px">loading…</span></div></td>`;
    tbody.appendChild(detailTr);

    if (hasMerchants) {
      document.getElementById(`cbtn-${i}`).addEventListener('click', () => toggleExpand(`cdetail-${i}`, `cbtn-${i}`, async () => {
        const r = await apiGet('category_merchants', {category: row.name});
        document.getElementById(`cmerchants-${i}`).innerHTML = (r.merchants||[]).length
          ? r.merchants.map(m=>`<span class="pill" title="${esc(m)}">${esc(m)}</span>`).join('')
          : '<span style="color:var(--muted);font-size:11px">no merchants</span>';
      }));
    }
  });
}

async function addCategoryRow() {
  if (_editInProgress) return;
  const tbody = document.getElementById('cat-tbody');
  if (!tbody) return;
  tbody.querySelector('.new-row')?.remove();
  const tr = document.createElement('tr');
  tr.className = 'new-row';
  tr.innerHTML = `
    <td><input id="nc-name" type="text" placeholder="category name"></td>
    <td colspan="2"></td>
    <td>
      <button class="btn" style="padding:3px 9px;font-size:11px" id="nc-save">✓</button>
      <button class="btn ghost" style="padding:3px 7px;font-size:11px;margin-left:3px" onclick="this.closest('tr').remove()">✕</button>
    </td>
  `;
  tbody.insertBefore(tr, tbody.firstChild);
  document.getElementById('nc-name').focus();
  document.getElementById('nc-save').addEventListener('click', async () => {
    const name = document.getElementById('nc-name').value.trim();
    if (!name) { toast('Category name required','err'); return; }
    const r = await apiPost('insert', {table:'categories', data:{name}});
    if (r.error) { toast('Error: '+r.error,'err'); return; }
    toast('Added ✓'); renderTab();
  });
}

// ══════════════════════════════════════════════════════════════════
// EXPAND TOGGLE (generic)
// ══════════════════════════════════════════════════════════════════
const _expanded = new Set();
async function toggleExpand(detailId, btnId, loader) {
  const detail = document.getElementById(detailId);
  const btn    = document.getElementById(btnId);
  const isOpen = _expanded.has(detailId);
  if (isOpen) {
    detail.style.display = 'none';
    btn.classList.remove('open');
    btn.textContent = btn.textContent.replace('▴','▾');
    _expanded.delete(detailId);
  } else {
    detail.style.display = '';
    btn.classList.add('open');
    btn.textContent = btn.textContent.replace('▾','▴');
    _expanded.add(detailId);
    await loader();
  }
}

// ── boot
renderTab();
</script>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════
# DB
# ══════════════════════════════════════════════════════════════════

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def q_meta():
    with get_conn() as conn:
        cats  = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        accs  = conn.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
        mercs = conn.execute("SELECT id, name FROM merchants ORDER BY name").fetchall()
    return {
        "db_path":    str(DB_PATH),
        "categories": [{"id": r["id"], "name": r["name"]} for r in cats],
        "accounts":   [{"id": r["id"], "name": r["name"]} for r in accs],
        "merchants":  [{"id": r["id"], "name": r["name"]} for r in mercs],
    }


def q_transactions(params):
    page      = max(1, int(params.get("page",      [1])[0]))
    page_size = min(500, max(1, int(params.get("page_size", [50])[0])))
    sort_map  = {
        "date": "t.date", "description": "t.description",
        "merchant": "m.name", "category": "c.name",
        "account": "a.name", "amount": "t.amount", "notes": "t.notes",
    }
    sort_col = sort_map.get(params.get("sort", ["date"])[0], "t.date")
    sort_dir = "ASC" if params.get("dir", ["desc"])[0].lower() == "asc" else "DESC"
    q        = (params.get("q",   [""])[0] or "").strip()
    cat_id   = params.get("category", [""])[0]
    acc_id   = params.get("account",  [""])[0]
    amt_min  = params.get("amount_min", [""])[0]
    amt_max  = params.get("amount_max", [""])[0]

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
        LEFT JOIN merchants m  ON m.id  = t.merchant_id
        LEFT JOIN categories c ON c.id  = t.category_id
        LEFT JOIN accounts a   ON a.id  = t.account_id
        {clause}
    """
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
        rows  = conn.execute(
            f"SELECT t.id, t.date, t.description, t.amount, t.notes, "
            f"m.name as merchant, c.name as category, a.name as account {base} "
            f"ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size]
        ).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows]}


def q_merchants(params):
    page      = max(1, int(params.get("page",      [1])[0]))
    page_size = min(500, max(1, int(params.get("page_size", [50])[0])))
    sort_map  = {
        "name": "m.name", "category": "c.name",
        "alias_count": "alias_count", "tx_count": "tx_count", "created_at": "m.created_at",
    }
    sort_col = sort_map.get(params.get("sort", ["name"])[0], "m.name")
    sort_dir = "ASC" if params.get("dir", ["asc"])[0].lower() == "asc" else "DESC"
    q        = (params.get("q", [""])[0] or "").strip()

    where, args = [], []
    if q:
        where.append("m.name LIKE ?"); args.append(f"%{q}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM merchants m {clause}", args).fetchone()[0]
        rows  = conn.execute(f"""
            SELECT m.id, m.name, c.name as category, m.created_at,
                   COUNT(DISTINCT ma.id) as alias_count,
                   COUNT(DISTINCT t.id)  as tx_count
            FROM merchants m
            LEFT JOIN categories c       ON c.id  = m.category_id
            LEFT JOIN merchant_aliases ma ON ma.merchant_id = m.id
            LEFT JOIN transactions t      ON t.merchant_id  = m.id
            {clause}
            GROUP BY m.id
            ORDER BY {sort_col} {sort_dir}
            LIMIT ? OFFSET ?
        """, args + [page_size, (page - 1) * page_size]).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows]}


def q_categories():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name,
                   COUNT(DISTINCT m.id) as merchant_count,
                   COUNT(DISTINCT t.id) as tx_count
            FROM categories c
            LEFT JOIN merchants m    ON m.category_id = c.id
            LEFT JOIN transactions t ON t.category_id = c.id
            GROUP BY c.id
            ORDER BY tx_count DESC, c.name
        """).fetchall()
    return {"rows": [dict(r) for r in rows]}


def q_aliases(params):
    name = (params.get("merchant", [""])[0] or "").strip()
    if not name:
        return {"aliases": []}
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ma.raw_description
            FROM merchant_aliases ma JOIN merchants m ON m.id = ma.merchant_id
            WHERE m.name = ? ORDER BY ma.raw_description
        """, (name,)).fetchall()
    return {"aliases": [r["raw_description"] for r in rows]}


def q_category_merchants(params):
    name = (params.get("category", [""])[0] or "").strip()
    if not name:
        return {"merchants": []}
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.name FROM merchants m JOIN categories c ON c.id = m.category_id
            WHERE c.name = ? ORDER BY m.name
        """, (name,)).fetchall()
    return {"merchants": [r["name"] for r in rows]}


# ── WRITE operations ──────────────────────────────────────────────

_ALLOWED_TABLES  = {"transactions", "merchants", "categories", "accounts"}
_ALLOWED_FIELDS  = {
    "transactions": {"date", "description", "amount", "notes", "merchant_id", "category_id", "account_id"},
    "merchants":    {"name", "category_id"},
    "categories":   {"name"},
    "accounts":     {"name", "type", "currency"},
}

def q_update(body):
    table = body.get("table", "")
    id_   = body.get("id")
    field = body.get("field", "")
    value = body.get("value")
    if table not in _ALLOWED_TABLES:
        return {"error": "invalid table"}
    if field not in _ALLOWED_FIELDS.get(table, set()):
        return {"error": f"invalid field '{field}' for table '{table}'"}
    with get_conn() as conn:
        conn.execute(f"UPDATE {table} SET {field} = ? WHERE id = ?", (value, id_))
        conn.commit()
    return {"ok": True}


def q_delete(body):
    table = body.get("table", "")
    id_   = body.get("id")
    if table not in _ALLOWED_TABLES:
        return {"error": "invalid table"}
    with get_conn() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (id_,))
        conn.commit()
    return {"ok": True}


def q_insert(body):
    table = body.get("table", "")
    data  = body.get("data", {})
    if table not in _ALLOWED_TABLES:
        return {"error": "invalid table"}
    _INSERT_EXTRAS = {
        "transactions": {"date", "description", "amount", "notes", "merchant_id", "category_id", "account_id"},
        "merchants":    {"name", "category_id"},
        "categories":   {"name"},
        "accounts":     {"name", "type", "currency"},
    }
    allowed_insert = _INSERT_EXTRAS.get(table, set())
    clean = {k: v for k, v in data.items() if k in allowed_insert and v not in (None, "")}
    if not clean:
        return {"error": "no valid fields to insert"}
    cols = ", ".join(clean.keys())
    placeholders = ", ".join(["?"] * len(clean))
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
            list(clean.values())
        )
        conn.commit()
    return {"ok": True, "id": cur.lastrowid}


# ══════════════════════════════════════════════════════════════════
# HTTP HANDLER
# ══════════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

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

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path   = parsed.path
        try:
            if path in ("/", ""):
                self.send_html(HTML)
            elif path == "/api/meta":
                self.send_json(q_meta())
            elif path == "/api/transactions":
                self.send_json(q_transactions(params))
            elif path == "/api/merchants":
                self.send_json(q_merchants(params))
            elif path == "/api/categories":
                self.send_json(q_categories())
            elif path == "/api/aliases":
                self.send_json(q_aliases(params))
            elif path == "/api/category_merchants":
                self.send_json(q_category_merchants(params))
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_body()
            if path == "/api/update":
                self.send_json(q_update(body))
            elif path == "/api/delete":
                self.send_json(q_delete(body))
            elif path == "/api/insert":
                self.send_json(q_insert(body))
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)


# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════

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