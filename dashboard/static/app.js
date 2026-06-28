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
    return r.json();
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
// RENDER — BAR WITH BUDGET
// ══════════════════════════════════════════════════════════════════

function renderBarWithBudget(sec, prevLabel) {
    const maxVal = Math.max(
        ...sec.categories.map(c => Math.max(c.amount, c.prev, c.budget || 0)),
        1
    );

    const rows = sec.categories.map(cat => {
        const budget = cat.budget || 0;
        const isOver = budget && cat.amount > budget;
        const isWarn = budget && cat.amount > budget * 0.8 && !isOver;
        const fillCls = isOver ? 'over' : isWarn ? 'warning' : 'normal';

        const fillW = budget ? (Math.min(cat.amount, budget) / budget) * 100 : (cat.amount / maxVal) * 100;
        const prevW = budget ? (Math.min(cat.prev, budget) / budget) * 100 : (cat.prev / maxVal) * 100;
        const unusedW = budget && !isOver ? (100 - fillW) : 0;
        const overW = isOver ? ((cat.amount - budget) / budget) * 100 : 0;

        const overText = isOver ? `<span class="over">+${fmt(cat.amount - budget)} over</span>` : '';
        const budgetText = budget ? `<span class="budget-lbl">budget ${fmt(budget)}</span>` : '';

        return `
      <div class="bar-row">
        <div class="bar-row-labels">
          <span class="bar-cat-name">${cat.name}</span>
          <div class="bar-amounts">
            ${overText}
            <span class="this-month">${fmtDec(cat.amount)}</span>
            <span class="prev-month">${fmtDec(cat.prev)} prev</span>
            ${budgetText}
          </div>
        </div>
        <div class="bar-track">
          ${unusedW > 0 ? `<div class="bar-unused" style="left:${fillW}%;width:${unusedW}%"></div>` : ''}
          ${overW > 0 ? `<div class="bar-over-ext" style="left:100%;width:${overW}%"></div>` : ''}
          <div class="bar-prev" style="width:${prevW}%"></div>
          <div class="bar-fill ${fillCls}" style="width:${fillW + (isOver ? overW : 0)}%"></div>
          ${budget > 0 ? `<div class="bar-budget-marker" style="left:100%"></div>` : ''}
        </div>
      </div>
    `;
    }).join('');

    return `
    <div class="section-card">
      <div class="section-header">
        <span class="section-title">${sec.name}</span>
        <span class="section-subtitle">budgeted · vs ${prevLabel}</span>
      </div>
      <div class="section-body">${rows}</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:var(--accent)"></div>this month</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--border2)"></div>last month</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--accent-dim)"></div>unused budget</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--red-dim)"></div>over budget</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--muted2);width:2px"></div>budget limit</div>
      </div>
    </div>
  `;
}


// ══════════════════════════════════════════════════════════════════
// RENDER — BAR COMPARISON
// ══════════════════════════════════════════════════════════════════

function renderBarComparison(sec, prevLabel) {
    const maxVal = Math.max(...sec.categories.map(c => Math.max(c.amount, c.prev)), 1);

    const rows = sec.categories.map(cat => {
        const fillW = (cat.amount / maxVal) * 100;
        const prevW = (cat.prev / maxVal) * 100;
        const diff = cat.amount - cat.prev;
        const pct = cat.prev ? Math.round(Math.abs(diff / cat.prev) * 100) : null;
        const diffCls = diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat';
        const diffText = cat.prev && Math.abs(diff) > 0.5
            ? `<span class="${diffCls}">${diff > 0 ? '↑' : '↓'}${pct}%</span>`
            : '';

        return `
      <div class="bar-row">
        <div class="bar-row-labels">
          <span class="bar-cat-name">${cat.name}</span>
          <div class="bar-amounts">
            ${diffText}
            <span class="this-month">${fmtDec(cat.amount)}</span>
            <span class="prev-month">${fmtDec(cat.prev)} prev</span>
          </div>
        </div>
        <div class="bar-track">
          <div class="bar-prev" style="width:${prevW}%"></div>
          <div class="bar-fill normal" style="width:${fillW}%"></div>
        </div>
      </div>
    `;
    }).join('');

    return `
    <div class="section-card">
      <div class="section-header">
        <span class="section-title">${sec.name}</span>
        <span class="section-subtitle">vs ${prevLabel}</span>
      </div>
      <div class="section-body">${rows}</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:var(--accent)"></div>this month</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--border2)"></div>last month</div>
      </div>
    </div>
  `;
}


// ══════════════════════════════════════════════════════════════════
// RENDER — NUMBERS
// ══════════════════════════════════════════════════════════════════

function renderNumbers(sec, prevLabel) {
    const cells = sec.categories.map(cat => {
        const diff = cat.amount - cat.prev;
        const pct = cat.prev ? Math.round(Math.abs(diff / cat.prev) * 100) : null;
        const cls = diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat';
        const diffStr = cat.prev && Math.abs(diff) > 0.5
            ? `<span class="${cls}">${diff > 0 ? '↑' : '↓'}${pct}%</span> vs ${fmtDec(cat.prev)}`
            : cat.prev ? 'same as last month' : 'no prior data';

        return `
      <div class="number-cell">
        <div class="cat-label">${cat.name}</div>
        <div class="cat-value">${fmtDec(cat.amount)}</div>
        <div class="cat-prev">${diffStr}</div>
      </div>
    `;
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
    </div>
  `;
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
    const data = await fetchData(ym);
    renderDashboard(data);
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