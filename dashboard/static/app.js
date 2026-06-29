// ══════════════════════════════════════════════════════════════════
// UTILS
// ══════════════════════════════════════════════════════════════════

const fmt = (n) => '$' + Math.abs(n).toLocaleString('en-AU', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const fmtDec = (n) => '$' + Math.abs(n).toLocaleString('en-AU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function deltaHtml(current, prev, invertColour = false) {
  if (!prev) return `<span class="flat">no prior data</span>`;
  const diff = current - prev;
  const pct = Math.round(Math.abs(diff / prev) * 100);
  if (Math.abs(diff) < 0.5) return `<span class="flat">same as last month</span>`;
  const isUp = diff > 0;
  const cls = invertColour ? (isUp ? 'down' : 'up') : (isUp ? 'up' : 'down');
  const arrow = isUp ? '↑' : '↓';
  return `<span class="${cls}">${arrow}${pct}% vs last month (${fmt(prev)})</span>`;
}

function monthLabel(ym) {
  if (!ym) return '';
  const [y, m] = ym.split('-').map(Number);
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${names[m - 1]} ${y}`;
}

// ══════════════════════════════════════════════════════════════════
// DATA
// ══════════════════════════════════════════════════════════════════

async function fetchData(ym) {
  const r = await fetch(`/api/dashboard?month=${encodeURIComponent(ym)}`);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || 'API error');
  return data;
}

async function fetchMonths() {
  const r = await fetch('/api/months');
  return r.json();
}

async function fetchCategoryTransactions(ym, category) {
  const r = await fetch(
    `/api/category_transactions?month=${encodeURIComponent(ym)}&category=${encodeURIComponent(category)}`
  );
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || 'API error');
  return data;
}

// ══════════════════════════════════════════════════════════════════
// MODAL
// ══════════════════════════════════════════════════════════════════

function openTransactionModal(ym, category) {
  // Remove any existing modal
  document.getElementById('tx-modal')?.remove();

  const overlay = document.createElement('div');
  overlay.id = 'tx-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal-panel">
      <div class="modal-header">
        <div class="modal-title">
          <span class="modal-category">${category}</span>
          <span class="modal-month">${monthLabel(ym)}</span>
        </div>
        <button class="modal-close" id="modal-close-btn">✕</button>
      </div>
      <div class="modal-body" id="modal-body">
        <div class="modal-loading">loading…</div>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  // Close on backdrop click or × button
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeModal();
  });
  document.getElementById('modal-close-btn').addEventListener('click', closeModal);

  // Close on Escape
  const onKey = (e) => { if (e.key === 'Escape') closeModal(); };
  document.addEventListener('keydown', onKey);
  overlay._onKey = onKey;

  // Trigger animation
  requestAnimationFrame(() => overlay.classList.add('open'));

  // Fetch and render
  fetchCategoryTransactions(ym, category)
    .then(data => renderModalBody(data))
    .catch(err => {
      document.getElementById('modal-body').innerHTML =
        `<div class="modal-error">Failed to load: ${err.message}</div>`;
    });
}

function closeModal() {
  const overlay = document.getElementById('tx-modal');
  if (!overlay) return;
  document.removeEventListener('keydown', overlay._onKey);
  overlay.classList.remove('open');
  overlay.addEventListener('transitionend', () => overlay.remove(), { once: true });
}

function renderModalBody(data) {
  const body = document.getElementById('modal-body');
  if (!body) return;

  const txs = data.transactions || [];
  if (!txs.length) {
    body.innerHTML = `<div class="modal-empty">No transactions for this category.</div>`;
    return;
  }

  const net = txs.reduce((sum, t) => sum + t.amount, 0);
  const isNetCredit = net > 0;

  const rows = txs.map(t => {
    const label = t.merchant || t.description || '—';
    const shortLabel = label.length > 42 ? label.slice(0, 42) + '…' : label;
    const isCredit = t.amount > 0;
    const amtCls = isCredit ? 'tx-amount credit' : 'tx-amount';
    const amtStr = isCredit ? `+${fmtDec(t.amount)}` : fmtDec(Math.abs(t.amount));
    return `
      <tr>
        <td class="tx-date">${t.date}</td>
        <td class="tx-merchant" title="${esc(label)}">${esc(shortLabel)}</td>
        <td class="${amtCls}">${amtStr}</td>
      </tr>`;
  }).join('');

  const netLabel = isNetCredit ? 'Net (credit)' : 'Net';
  const netAmtCls = isNetCredit ? 'tx-total-amount credit' : 'tx-total-amount';
  const netAmtStr = isNetCredit ? `+${fmtDec(net)}` : fmtDec(Math.abs(net));

  body.innerHTML = `
    <table class="modal-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Merchant / Description</th>
          <th>Amount</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
      <tfoot>
        <tr>
          <td colspan="2" class="tx-total-label">${netLabel}</td>
          <td class="${netAmtCls}">${netAmtStr}</td>
        </tr>
      </tfoot>
    </table>
  `;
}

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ══════════════════════════════════════════════════════════════════
// RENDER — SUMMARY
// ══════════════════════════════════════════════════════════════════

function renderSummary(data) {
  const net = data.income - data.total_spend;
  const netCls = net >= 0 ? 'income' : 'expense';
  return `
    <div class="summary-strip">
      <div class="summary-card">
        <div class="label">Income</div>
        <div class="value income">${fmtDec(data.income)}</div>
        <div class="delta">${deltaHtml(data.income, data.prev_income, true)}</div>
      </div>
      <div class="summary-card">
        <div class="label">Total Spending</div>
        <div class="value expense">${fmtDec(data.total_spend)}</div>
        <div class="delta">${deltaHtml(data.total_spend, data.prev_total)}</div>
      </div>
      <div class="summary-card">
        <div class="label">Net</div>
        <div class="value ${netCls}">${net >= 0 ? '+' : '−'}${fmtDec(Math.abs(net))}</div>
        <div class="delta" style="color:var(--muted)">income minus spending</div>
      </div>
    </div>
  `;
}

// ══════════════════════════════════════════════════════════════════
// VERTICAL BAR CHART — shared renderer
// ══════════════════════════════════════════════════════════════════

function renderVerticalBars(sec, currentYm, prevYm, hasBudget) {
  const prevLabel = monthLabel(prevYm);
  const cats = sec.categories;

  const groups = cats.map(cat => {
    const budget = hasBudget ? (cat.budget || 0) : 0;
    const scaleMax = Math.max(cat.amount, cat.prev, budget, 1);

    const thisH = (cat.amount / scaleMax) * 100;
    const prevH = (cat.prev / scaleMax) * 100;

    const isOver = budget && cat.amount > budget;
    const isWarn = budget && cat.amount > budget * 0.8 && !isOver;
    const barCls = isOver ? 'bar-v-fill over' : isWarn ? 'bar-v-fill warning' : 'bar-v-fill normal';

    const budgetPct = budget ? (budget / scaleMax) * 100 : null;

    // % change label — always render something
    const diff = cat.amount - cat.prev;
    let diffLabel;
    if (!cat.prev && !cat.amount) {
      diffLabel = `<span class="bar-v-pct flat">—</span>`;
    } else if (!cat.prev) {
      diffLabel = `<span class="bar-v-pct flat">new</span>`;
    } else if (Math.abs(diff) < 0.5) {
      diffLabel = `<span class="bar-v-pct flat">no change</span>`;
    } else {
      const pct = Math.round(Math.abs(diff / cat.prev) * 100);
      const cls = diff > 0 ? 'up' : 'down';
      const arrow = diff > 0 ? '↑' : '↓';
      diffLabel = `<span class="bar-v-pct ${cls}">${arrow}${pct}%</span>`;
    }

    const budgetLine = budgetPct !== null ? `
      <div class="bar-v-budget-line" style="bottom:${budgetPct}%">
        <span class="bar-v-budget-label">${fmt(budget)}</span>
      </div>` : '';

    // data attributes carry what to open on click
    return `
      <div class="bar-v-group">
        <div class="bar-v-chart">
          ${budgetLine}
          <!-- prev bar -->
          <div class="bar-v-col prev-col">
            <div class="bar-v-amount prev-amt">${fmt(cat.prev)}</div>
            <div class="bar-v-bar-wrap">
              <div class="bar-v-fill prev clickable-bar"
                   style="height:${prevH}%"
                   data-ym="${prevYm}"
                   data-category="${esc(cat.name)}"
                   title="${esc(cat.name)} · ${monthLabel(prevYm)}"></div>
            </div>
          </div>
          <!-- this month bar -->
          <div class="bar-v-col this-col">
            <div class="bar-v-amount this-amt">${fmtDec(cat.amount)}</div>
            <div class="bar-v-bar-wrap">
              <div class="${barCls} clickable-bar"
                   style="height:${thisH}%"
                   data-ym="${currentYm}"
                   data-category="${esc(cat.name)}"
                   title="${esc(cat.name)} · ${monthLabel(currentYm)}"></div>
            </div>
          </div>
        </div>
        <div class="bar-v-xlabel">
          <span class="bar-v-catname">${cat.name}</span>
          ${diffLabel}
        </div>
      </div>`;
  }).join('');

  const subtitle = hasBudget ? `budgeted · vs ${prevLabel}` : `vs ${prevLabel}`;

  return `
    <div class="section-card">
      <div class="section-header">
        <span class="section-title">${sec.name}</span>
        <span class="section-subtitle">${subtitle}</span>
      </div>
      <div class="section-body bar-v-section">
        ${groups}
      </div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:var(--accent)"></div>this month</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--surface3)"></div>${prevLabel}</div>
        ${hasBudget ? `<div class="legend-item"><div class="legend-dot" style="background:var(--muted2);height:2px;width:14px;border-radius:0"></div>budget</div>` : ''}
      </div>
    </div>`;
}

function renderBarWithBudget(sec, currentYm, prevYm) {
  return renderVerticalBars(sec, currentYm, prevYm, true);
}

function renderBarComparison(sec, currentYm, prevYm) {
  return renderVerticalBars(sec, currentYm, prevYm, false);
}

// ══════════════════════════════════════════════════════════════════
// RENDER — NUMBERS
// ══════════════════════════════════════════════════════════════════

function renderNumbers(sec, currentYm, prevYm) {
  const prevLabel = monthLabel(prevYm);
  const cells = sec.categories.map(cat => {
    const prevStr = cat.prev
      ? `<span class="num-prev-val">${fmtDec(cat.prev)}</span> <span class="num-prev-label">prev</span>`
      : `<span class="num-prev-label">no prior data</span>`;

    return `
      <div class="number-cell clickable-tile"
           data-ym="${currentYm}"
           data-category="${esc(cat.name)}"
           data-prev-ym="${prevYm}"
           title="Click to see transactions">
        <div class="cat-label">${cat.name}</div>
        <div class="cat-value">${fmtDec(cat.amount)}</div>
        <div class="cat-prev">${prevStr}</div>
      </div>`;
  }).join('');

  return `
    <div class="section-card">
      <div class="section-header">
        <span class="section-title">${sec.name}</span>
        <span class="section-subtitle">vs ${prevLabel}</span>
      </div>
      <div class="section-body">
        <div class="number-grid">${cells}</div>
      </div>
    </div>`;
}

// ══════════════════════════════════════════════════════════════════
// RENDER — DASHBOARD + EVENT DELEGATION
// ══════════════════════════════════════════════════════════════════

function renderDashboard(data) {
  const currentYm = data.month;
  const prevYm = data.prev_month;

  const sections = data.sections.map(sec => {
    if (sec.display === 'bar_with_budget') return renderBarWithBudget(sec, currentYm, prevYm);
    if (sec.display === 'number') return renderNumbers(sec, currentYm, prevYm);
    return renderBarComparison(sec, currentYm, prevYm);
  }).join('');

  const page = document.getElementById('page');
  page.innerHTML = renderSummary(data) + sections;

  // Single delegated listener for all bars and number tiles
  page.addEventListener('click', (e) => {
    const bar = e.target.closest('.clickable-bar');
    const tile = e.target.closest('.clickable-tile');

    if (bar) {
      const ym = bar.dataset.ym;
      const cat = bar.dataset.category;
      if (ym && cat) openTransactionModal(ym, cat);
    } else if (tile) {
      // Number tiles: current month on primary area; could add prev later
      const ym = tile.dataset.ym;
      const cat = tile.dataset.category;
      if (ym && cat) openTransactionModal(ym, cat);
    }
  });
}

// ══════════════════════════════════════════════════════════════════
// BOOT
// ══════════════════════════════════════════════════════════════════

async function loadMonth(ym) {
  document.getElementById('page').innerHTML = '<div class="loading-screen">loading…</div>';
  try {
    const data = await fetchData(ym);
    if (!data || !data.sections) {
      document.getElementById('page').innerHTML =
        `<div class="empty-state">API returned unexpected data.<br><pre style="font-size:10px;margin-top:8px;color:var(--muted)">${JSON.stringify(data, null, 2)}</pre></div>`;
      return;
    }
    renderDashboard(data);
  } catch (e) {
    document.getElementById('page').innerHTML =
      `<div class="empty-state">Error loading dashboard:<br><pre style="font-size:10px;margin-top:8px;color:var(--red)">${e.message}</pre></div>`;
  }
}

async function boot() {
  const { months } = await fetchMonths();
  const sel = document.getElementById('month-select');

  if (!months || !months.length) {
    document.getElementById('page').innerHTML =
      '<div class="empty-state">No transactions found.<br>Run the ingester first.</div>';
    return;
  }

  months.forEach(ym => {
    const opt = document.createElement('option');
    opt.value = ym;
    opt.textContent = monthLabel(ym);
    sel.appendChild(opt);
  });

  const now = new Date();
  const nowYm = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const target = months.includes(nowYm) ? nowYm : months[0];
  sel.value = target;
  loadMonth(target);
}

boot();