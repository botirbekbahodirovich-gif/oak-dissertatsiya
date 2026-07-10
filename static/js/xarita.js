/* Mening dissertatsiya xaritam — client renderer.
 * Fetches /api/xarita/data, draws the year timeline (Chart.js), the AI niche
 * analysis and a client-side-paginated students table with bookmark buttons. */
(function () {
  'use strict';

  var root = document.getElementById('xarita-root');
  if (!root) return;

  var PER_PAGE = 10;
  var savedSupervisor = (root.getAttribute('data-supervisor') || '').trim();

  var state = { rows: [], page: 1, chart: null };

  // ── elements ──
  var elForm       = document.getElementById('xr-form');
  var elInput      = document.getElementById('supervisor-input');
  var elSearchBtn  = document.getElementById('search-btn');
  var elFormMsg    = document.getElementById('xr-form-msg');
  var elLoading    = document.getElementById('xr-loading');
  var elResults    = document.getElementById('xr-results');
  var elConfirm    = document.getElementById('xr-confirm');
  var elConfirmName= document.getElementById('xr-confirm-name');
  var elConfirmBtn = document.getElementById('confirm-btn');
  var elChangeBtn  = document.getElementById('change-supervisor-btn');
  var elTbody      = document.getElementById('shogirdlar-tbody');
  var elEmpty      = document.getElementById('xr-empty');
  var elPager      = document.getElementById('xarita-pagination');
  var elAiBody     = document.getElementById('xr-ai-body');
  var elAiCached   = document.getElementById('xr-ai-cached');

  // ── helpers ──
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function show(el) { if (el) el.classList.remove('hidden'); }
  function hide(el) { if (el) el.classList.add('hidden'); }

  // ── data load ──
  function loadData(name, isSaved) {
    name = (name || '').trim();
    if (name.length < 3) {
      elFormMsg.textContent = 'Iltimos, kamida 3 ta belgi kiriting.';
      return;
    }
    hide(elForm);
    hide(elResults);
    show(elLoading);
    fetch('/api/xarita/data?supervisor=' + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        hide(elLoading);
        if (!d || !d.ok) {
          showForm('Rahbar topilmadi yoki so\'rovda xatolik. Qayta urinib ko\'ring.');
          return;
        }
        render(d, isSaved);
      })
      .catch(function () {
        hide(elLoading);
        showForm('Tarmoq xatosi. Iltimos, keyinroq urinib ko\'ring.');
      });
  }

  function showForm(msg) {
    hide(elResults);
    show(elForm);
    elFormMsg.textContent = msg || '';
  }

  // ── render ──
  function render(d, isSaved) {
    var sup = d.supervisor || {};
    state.rows = d.shogirdlar || [];
    state.page = 1;

    // stat cards
    document.getElementById('stat-name').textContent = sup.name || '—';
    document.getElementById('stat-total').textContent = sup.total_students || 0;
    document.getElementById('stat-years').textContent = sup.years_active || '—';
    document.getElementById('stat-top-ixt').textContent = sup.top_ixtisoslik || '—';

    // confirm bar — only in preview mode (an unsaved supervisor)
    if (isSaved) {
      hide(elConfirm);
    } else {
      elConfirmName.textContent = sup.name || '';
      elConfirm.setAttribute('data-name', sup.name || '');
      show(elConfirm);
    }

    // AI
    if (d.ai_tahlil) {
      elAiBody.textContent = d.ai_tahlil;
    } else {
      elAiBody.textContent = 'Tahlil mavjud emas.';
    }
    if (d.cached) { show(elAiCached); } else { hide(elAiCached); }

    renderChart(d.yillar || []);
    renderTable();
    show(elResults);
  }

  function renderChart(yillar) {
    var canvas = document.getElementById('xarita-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    var labels = yillar.map(function (y) { return y.year; });
    var data = yillar.map(function (y) { return y.count; });
    var css = getComputedStyle(document.documentElement);
    var accent = (css.getPropertyValue('--oak-accent') || '#3b82f6').trim() || '#3b82f6';
    var muted = (css.getPropertyValue('--oak-muted') || '#94a3b8').trim() || '#94a3b8';
    state.chart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'Dissertatsiyalar',
          data: data,
          backgroundColor: accent,
          borderRadius: 6,
          maxBarThickness: 46
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { color: muted } },
          y: { beginAtZero: true, ticks: { color: muted, precision: 0 },
               grid: { color: 'rgba(148,163,184,0.12)' } }
        }
      }
    });
  }

  // ── students table + pagination ──
  function renderTable() {
    var rows = state.rows;
    elTbody.innerHTML = '';
    if (!rows.length) {
      show(elEmpty);
      elPager.innerHTML = '';
      return;
    }
    hide(elEmpty);

    var totalPages = Math.ceil(rows.length / PER_PAGE);
    if (state.page > totalPages) state.page = totalPages;
    var start = (state.page - 1) * PER_PAGE;
    var slice = rows.slice(start, start + PER_PAGE);

    slice.forEach(function (r) {
      var tr = document.createElement('tr');
      var isDsc = (r.daraja || '').toUpperCase() === 'DSC';
      var olimLink = r.olim
        ? '<a href="/olim/' + encodeURIComponent(r.olim) + '" target="_blank" rel="noopener">' + esc(r.olim) + '</a>'
        : '<span class="text-muted">—</span>';
      tr.innerHTML =
        '<td class="xr-mavzu">' + esc(r.mavzu || '—') + '</td>' +
        '<td>' + olimLink + '</td>' +
        '<td>' + esc(r.yil || '—') + '</td>' +
        '<td><span class="xr-badge' + (isDsc ? ' dsc' : '') + '">' + esc(r.daraja || '—') + '</span></td>' +
        '<td>' + esc(r.ixtisoslik || '—') + '</td>' +
        '<td><button class="xr-bm" data-id="' + esc(r.id) + '" title="Saqlash">☆</button></td>';
      elTbody.appendChild(tr);
    });
    renderPager(totalPages);
  }

  function renderPager(totalPages) {
    elPager.innerHTML = '';
    if (totalPages <= 1) return;

    function btn(label, page, opts) {
      opts = opts || {};
      var b = document.createElement('button');
      b.textContent = label;
      if (opts.active) b.classList.add('active');
      if (opts.disabled) b.disabled = true;
      else b.addEventListener('click', function () {
        state.page = page;
        renderTable();
        document.querySelector('.xr-table-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      elPager.appendChild(b);
    }

    btn('‹', state.page - 1, { disabled: state.page === 1 });
    // windowed page numbers
    var from = Math.max(1, state.page - 2);
    var to = Math.min(totalPages, from + 4);
    from = Math.max(1, Math.min(from, to - 4));
    if (from > 1) { btn('1', 1, {}); if (from > 2) addEllipsis(); }
    for (var p = from; p <= to; p++) btn(String(p), p, { active: p === state.page });
    if (to < totalPages) { if (to < totalPages - 1) addEllipsis(); btn(String(totalPages), totalPages, {}); }
    btn('›', state.page + 1, { disabled: state.page === totalPages });
  }

  function addEllipsis() {
    var span = document.createElement('button');
    span.textContent = '…'; span.disabled = true; span.style.border = 'none'; span.style.background = 'none';
    elPager.appendChild(span);
  }

  // ── bookmark toggle (delegated) ──
  elTbody.addEventListener('click', function (e) {
    var b = e.target.closest('.xr-bm');
    if (!b) return;
    var id = b.getAttribute('data-id');
    if (!id) return;
    b.disabled = true;
    fetch('/api/bookmarks/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dissertation_id: parseInt(id, 10) })
    })
      .then(function (r) {
        if (r.status === 401) { window.location.href = '/login'; return null; }
        return r.json();
      })
      .then(function (d) {
        b.disabled = false;
        if (!d || !d.success) return;
        b.textContent = d.bookmarked ? '★' : '☆';
        b.title = d.bookmarked ? 'Saqlangan' : 'Saqlash';
      })
      .catch(function () { b.disabled = false; });
  });

  // ── save chosen supervisor ──
  elConfirmBtn.addEventListener('click', function () {
    var name = elConfirm.getAttribute('data-name') || '';
    if (!name) return;
    elConfirmBtn.disabled = true;
    elConfirmBtn.textContent = 'Saqlanmoqda...';
    fetch('/xarita/supervisor/set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ supervisor_name: name })
    })
      .then(function (r) {
        if (r.status === 401) { window.location.href = '/login'; return null; }
        return r.json();
      })
      .then(function (d) {
        elConfirmBtn.disabled = false;
        elConfirmBtn.textContent = '✓ Tasdiqlash';
        if (d && d.ok) {
          savedSupervisor = d.supervisor;
          hide(elConfirm);
        } else {
          alert((d && d.error) || 'Saqlashda xatolik.');
        }
      })
      .catch(function () {
        elConfirmBtn.disabled = false;
        elConfirmBtn.textContent = '✓ Tasdiqlash';
      });
  });

  // ── change supervisor ──
  elChangeBtn.addEventListener('click', function () {
    if (elInput) elInput.value = savedSupervisor || '';
    showForm('');
    if (elInput) elInput.focus();
  });

  // ── search ──
  elSearchBtn.addEventListener('click', function () {
    loadData(elInput.value, false);
  });
  elInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); loadData(elInput.value, false); }
  });

  // ── init ──
  if (savedSupervisor) {
    loadData(savedSupervisor, true);
  } else {
    showForm('');
  }
})();
