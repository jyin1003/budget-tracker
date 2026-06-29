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
  if (!r.ok) {
    console.error('API error:', data);
    throw new Error(data.error || 'API error');
  }
  return data;
}

async function fetchMonths() {
  const r = await fetch('/api/months');
  return r.json();
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
// hasBudget: whether to draw budget lines
// ══════════════════════════════════════════════════════════════════

function renderVerticalBars(sec, prevLabel, hasBudget) {
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

    return `
        <div class="bar-v-group">
          <div class="bar-v-chart">
            ${budgetLine}
            <!-- prev bar -->
            <div class="bar-v-col prev-col">
              <div class="bar-v-amount prev-amt">${fmt(cat.prev)}</div>
              <div class="bar-v-bar-wrap">
                <div class="bar-v-fill prev" style="height:${prevH}%"></div>
              </div>
            </div>
            <!-- this month bar -->
            <div class="bar-v-col this-col">
              <div class="bar-v-amount this-amt">${fmtDec(cat.amount)}</div>
              <div class="bar-v-bar-wrap">
                <div class="${barCls}" style="height:${thisH}%"></div>
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

// ══════════════════════════════════════════════════════════════════
// RENDER — BAR WITH BUDGET
// ══════════════════════════════════════════════════════════════════

function renderBarWithBudget(sec, prevLabel) {
  return renderVerticalBars(sec, prevLabel, true);
}

// ══════════════════════════════════════════════════════════════════
// RENDER — BAR COMPARISON
// ══════════════════════════════════════════════════════════════════

function renderBarComparison(sec, prevLabel) {
  return renderVerticalBars(sec, prevLabel, false);
}

// ══════════════════════════════════════════════════════════════════
// RENDER — NUMBERS
// ══════════════════════════════════════════════════════════════════

function renderNumbers(sec, prevLabel) {
  const cells = sec.categories.map(cat => {
    const prevStr = cat.prev
      ? `<span class="num-prev-val">${fmtDec(cat.prev)}</span> <span class="num-prev-label">prev</span>`
      : `<span class="num-prev-label">no prior data</span>`;

    return `
      <div class="number-cell">
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
// RENDER — DASHBOARD
// ══════════════════════════════════════════════════════════════════

function renderDashboard(data) {
  const prevLabel = monthLabel(data.prev_month);
  const sections = data.sections.map(sec => {
    if (sec.display === 'bar_with_budget') return renderBarWithBudget(sec, prevLabel);
    if (sec.display === 'number') return renderNumbers(sec, prevLabel);
    return renderBarComparison(sec, prevLabel);
  }).join('');

  document.getElementById('page').innerHTML = renderSummary(data) + sections;
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