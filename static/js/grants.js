/* Grants module v2 — filtrlash, kuzatuv, countdown, checklist, tablar.
   Sahifalar: /grants (grid + kanban), /grants/<slug> (detal).
   Deadline ogohlantirishlari: base.html'dagi global sessiya popupi allaqachon
   /api/v1/grants/reminders dan xabar beradi; bu fayl /grants sahifasida
   qo'shimcha inline banner ko'rsatadi (sessionStorage bilan yopiladi). */
(function () {
  'use strict';

  var S = window.GR_STATE || {};

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }
  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  /* ── 1. Filter system (AJAX) ─────────────────────────────────────────── */
  var filters = { country: '', level: '', funding: '', sort: 'deadline', search: '', page: 1 };

  function applyFilters() {
    var grid = document.getElementById('gr-grid');
    if (!grid) return;
    var p = new URLSearchParams();
    if (filters.country) p.set('country', filters.country);
    if (filters.level) p.set('level', filters.level);
    if (filters.funding) p.set('funding', filters.funding);
    if (filters.search) p.set('search', filters.search);
    p.set('sort', filters.sort);
    p.set('page', filters.page);
    grid.style.opacity = '0.5';
    fetch('/api/grants?' + p.toString())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        grid.style.opacity = '';
        renderGrid(d.grants || []);
        renderPagination(d.page || 1, d.pages || 1, d.total || 0);
        updateClearBtn();
      })
      .catch(function () { grid.style.opacity = ''; });
  }
  window.applyFilters = applyFilters;

  function clearFilters() {
    filters = { country: '', level: '', funding: '', sort: 'deadline', search: '', page: 1 };
    var c = document.getElementById('f-country'); if (c) c.value = '';
    var s = document.getElementById('f-sort'); if (s) s.value = 'deadline';
    var q = document.getElementById('gr-search'); if (q) q.value = '';
    document.querySelectorAll('.gr-toggle-group').forEach(function (g) {
      g.querySelectorAll('.gr-toggle').forEach(function (b, i) {
        b.classList.toggle('active', i === 0);
      });
    });
    applyFilters();
  }
  window.clearFilters = clearFilters;

  function updateClearBtn() {
    var btn = document.getElementById('f-clear');
    if (!btn) return;
    btn.style.display = (filters.country || filters.level || filters.funding ||
                         filters.search || filters.sort !== 'deadline') ? '' : 'none';
  }

  /* ── 2. Search with 300ms debounce ───────────────────────────────────── */
  var searchTimer;
  function searchGrants(query) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      filters.search = (query || '').trim();
      filters.page = 1;
      applyFilters();
    }, 300);
  }
  window.searchGrants = searchGrants;

  /* ── card renderer (grants.html macro'sining JS aksi) ────────────────── */
  function renderCard(g) {
    var urgent = g.days_remaining !== null && g.days_remaining >= 0 && g.days_remaining < 7;
    var color = g.days_remaining > 7 ? '#4ade80' : (g.days_remaining >= 3 ? '#fbbf24' : '#f87171');
    var pct = Math.min(100, Math.max(5, 100 - Math.floor(g.days_remaining * 100 / 60)));
    var deadlineHtml;
    if (g.days_remaining !== null && !g.expired) {
      deadlineHtml =
        '<div class="gr-deadline-txt"><span style="color:' + color + ';" class="countdown" data-deadline="' + esc(g.deadline) + '">⏰ ' +
        g.days_remaining + ' kun qoldi</span><span style="color:#64748b;">' + esc(g.deadline_uz) + '</span></div>' +
        '<div class="gr-bar"><i style="width:' + pct + '%;background:' + color + ';"></i></div>';
    } else if (g.expired) {
      deadlineHtml = '<div class="gr-deadline-txt"><span style="color:#94a3b8;">Muddat tugagan</span>' +
        '<span style="color:#64748b;">' + esc(g.deadline_uz) + '</span></div>';
    } else {
      deadlineHtml = '<div class="gr-deadline-txt"><span style="color:#94a3b8;">Doimiy ochiq</span></div>';
    }
    var badge = g.expired ? '<span class="gr-badge expired-badge">Muddat tugagan</span>'
      : (g.is_featured ? '<span class="gr-badge">🌟 Tavsiya</span>' : '');
    var meta1 = '<span>' + esc(g.country_flag) + ' ' + esc(g.country) + '</span>' +
      (g.levels_uz && g.levels_uz.length ? '<span>🎓 ' + esc(g.levels_uz.join(', ')) + '</span>' : '') +
      (g.funding_label ? '<span>' + esc(g.funding_label) + '</span>' : '');
    var meta2 = (g.stipend_amount ? '<span>💰 ' + esc(g.stipend_amount) + '</span>' : '') +
      (g.duration ? '<span>📅 ' + esc(g.duration) + '</span>' : '');
    var tracked = !!g.track_status;
    return '<div class="gr-card' + (urgent ? ' urgent' : '') + (g.expired ? ' expired' : '') + '" data-grant-id="' + g.id + '">' +
      '<div class="gr-cover"' + (g.cover_image_url ? ' style="background-image:url(\'' + esc(g.cover_image_url) + '\');"' : '') + '>' + badge + '</div>' +
      '<div class="gr-body">' +
      '<div class="gr-org">🏛️ ' + esc(g.organization || '—') + '</div>' +
      '<div class="gr-title-l"><a href="/grants/' + encodeURIComponent(g.slug) + '">📌 ' + esc(g.display_title || g.title) + '</a></div>' +
      '<div class="gr-meta">' + meta1 + '</div>' +
      (meta2 ? '<div class="gr-meta">' + meta2 + '</div>' : '') +
      '<div class="gr-deadline">' + deadlineHtml + '</div>' +
      '<div class="gr-actions">' +
      '<a class="gr-btn primary" href="/grants/' + encodeURIComponent(g.slug) + '">Batafsil →</a>' +
      '<button class="gr-btn track' + (tracked ? ' tracked' : '') + '" onclick="toggleTrackGrant(' + g.id + ', this)">' +
      (tracked ? '✅ Kuzatilmoqda' : '⭐ Kuzatish') + '</button>' +
      '</div></div></div>';
  }

  function renderGrid(items) {
    var grid = document.getElementById('gr-grid');
    var empty = document.getElementById('gr-empty');
    if (!grid) return;
    grid.innerHTML = items.map(renderCard).join('');
    if (empty) empty.style.display = items.length ? 'none' : '';
  }

  /* ── 10. Pagination ──────────────────────────────────────────────────── */
  function loadPage(page) {
    filters.page = page;
    applyFilters();
    var bar = document.getElementById('gr-filterbar');
    if (bar) bar.scrollIntoView({ behavior: 'smooth' });
  }
  window.loadPage = loadPage;

  function renderPagination(page, pages, total) {
    var el = document.getElementById('gr-pagination');
    var res = document.getElementById('gr-results');
    if (!el) return;
    var html = '';
    if (pages > 1) {
      if (page > 1) html += '<button class="gr-page-btn" onclick="loadPage(' + (page - 1) + ')">‹</button>';
      for (var i = 1; i <= pages; i++) {
        if (pages > 9 && i > 2 && i < pages - 1 && Math.abs(i - page) > 1) {
          if (html.slice(-10) !== '<span>…</span>'.slice(-10)) html += '<span>…</span>';
          continue;
        }
        html += '<button class="gr-page-btn' + (i === page ? ' active' : '') + '" onclick="loadPage(' + i + ')">' + i + '</button>';
      }
      if (page < pages) html += '<button class="gr-page-btn" onclick="loadPage(' + (page + 1) + ')">›</button>';
    }
    el.innerHTML = html;
    if (res) {
      var from = total ? (page - 1) * 12 + 1 : 0;
      var to = Math.min(page * 12, total);
      res.textContent = 'Natijalar: ' + from + '-' + to + ' / ' + total;
    }
  }

  /* ── 3. Countdown updater (har 60 soniyada) ──────────────────────────── */
  function updateCountdowns() {
    var now = new Date();
    document.querySelectorAll('.countdown[data-deadline]').forEach(function (el) {
      var dl = new Date(el.getAttribute('data-deadline') + 'T23:59:59');
      var days = Math.floor((dl - now) / 86400000);
      if (days < 0) { el.textContent = 'Muddat tugagan'; el.style.color = '#94a3b8'; return; }
      el.textContent = '⏰ ' + (days === 0 ? 'Bugun oxirgi kun!' : days + ' kun qoldi');
      el.style.color = days > 7 ? '#4ade80' : (days >= 3 ? '#fbbf24' : '#f87171');
    });
    // detal sahifadagi katta countdown (kun/soat/daqiqa)
    var big = document.getElementById('gd-countdown');
    if (big && big.getAttribute('data-deadline')) {
      var end = new Date(big.getAttribute('data-deadline') + 'T23:59:59');
      var diff = Math.max(0, end - now);
      var d = Math.floor(diff / 86400000);
      var h = Math.floor(diff % 86400000 / 3600000);
      var m = Math.floor(diff % 3600000 / 60000);
      var set = function (u, v) {
        var n = big.querySelector('[data-u="' + u + '"]');
        if (n) n.textContent = v;
      };
      set('d', d); set('h', h); set('m', m);
    }
  }
  window.updateCountdowns = updateCountdowns;

  /* ── 4. Track / untrack ──────────────────────────────────────────────── */
  function toggleTrackGrant(grantId, button) {
    if (!S.loggedIn) { window.location.href = '/login'; return; }
    var tracked = button.classList.contains('tracked');
    button.disabled = true;
    postJSON('/api/grants/' + grantId + '/track',
             tracked ? { action: 'untrack' } : { status: 'interested' })
      .then(function (d) {
        button.disabled = false;
        if (!d.success) return;
        button.classList.toggle('tracked', d.tracked);
        button.textContent = d.tracked ? '✅ Kuzatilmoqda' : (button.id === 'gd-track' ? '⭐ Kuzatishga olish' : '⭐ Kuzatish');
        var sel = document.getElementById('gd-status');
        if (sel) sel.style.display = d.tracked ? '' : 'none';
      })
      .catch(function () { button.disabled = false; });
  }
  window.toggleTrackGrant = toggleTrackGrant;

  /* ── 5. Update tracking status ───────────────────────────────────────── */
  function updateTrackingStatus(grantId, newStatus) {
    postJSON('/api/grants/' + grantId + '/update-status', { status: newStatus }).catch(function () {});
  }
  window.updateTrackingStatus = updateTrackingStatus;

  function saveNote(grantId, notes) {
    postJSON('/api/grants/' + grantId + '/update-status', { notes: notes }).catch(function () {});
  }
  window.saveNote = saveNote;

  /* ── 6. Deadline alerts — /grants sahifasida inline banner ───────────── */
  function checkDeadlineAlerts() {
    if (!S.loggedIn || sessionStorage.getItem('gr-alerts-dismissed')) return;
    var hero = document.querySelector('.gr-hero');
    if (!hero) return; // faqat grants sahifasida
    fetch('/api/grants/deadline-alerts')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var alerts = (d && d.alerts) || [];
        if (!alerts.length) return;
        var box = document.createElement('div');
        box.style.cssText = 'border:1px solid rgba(239,68,68,0.4);background:rgba(239,68,68,0.08);' +
          'border-radius:14px;padding:14px 18px;margin:14px 0;text-align:left;';
        var rows = alerts.slice(0, 4).map(function (a) {
          var dot = a.days_remaining <= 3 ? '🔴' : '🟡';
          return '<div style="margin:5px 0;">' + dot + ' <a href="/grants/' + encodeURIComponent(a.slug) +
            '" style="color:inherit;font-weight:700;">' + esc(a.title) + '</a> — ' +
            a.days_remaining + ' kun qoldi</div>';
        }).join('');
        box.innerHTML = '<b>⚠️ Muhim: grant muddatlari yaqinlashmoqda!</b>' + rows +
          '<div style="margin-top:8px;display:flex;gap:8px;">' +
          '<button class="gr-toggle" onclick="sessionStorage.setItem(\'gr-alerts-dismissed\',\'1\');this.closest(\'div\').parentElement.remove()">Tushunarli</button>' +
          '<a class="gr-toggle active" href="/grants?my=1" style="text-decoration:none;">Grantlarimni ko\'rish</a></div>';
        hero.parentElement.insertBefore(box, hero.nextSibling);
      })
      .catch(function () {});
  }
  window.checkDeadlineAlerts = checkDeadlineAlerts;

  /* ── 7. Share ────────────────────────────────────────────────────────── */
  function shareGrant(platform, url, title) {
    if (platform === 'telegram') {
      window.open('https://t.me/share/url?url=' + encodeURIComponent(url) +
                  '&text=' + encodeURIComponent(title), '_blank');
      return;
    }
    (navigator.clipboard ? navigator.clipboard.writeText(url)
                         : Promise.reject()).then(function () {
      alert('Havola nusxalandi ✓');
    }).catch(function () {
      prompt('Havolani nusxalang:', url);
    });
  }
  window.shareGrant = shareGrant;

  /* ── 8. Document checklist (localStorage) ────────────────────────────── */
  function docKey(grantId) { return 'gr-docs-' + grantId; }
  function toggleDocumentCheck(grantId, docIndex, input) {
    var saved = {};
    try { saved = JSON.parse(localStorage.getItem(docKey(grantId)) || '{}'); } catch (e) {}
    saved[docIndex] = input.checked;
    localStorage.setItem(docKey(grantId), JSON.stringify(saved));
    var row = input.closest('.gd-doc');
    if (row) row.classList.toggle('done', input.checked);
  }
  window.toggleDocumentCheck = toggleDocumentCheck;

  function restoreDocChecks() {
    if (!S.detailGrantId) return;
    var saved = {};
    try { saved = JSON.parse(localStorage.getItem(docKey(S.detailGrantId)) || '{}'); } catch (e) {}
    document.querySelectorAll('.gd-doc').forEach(function (row) {
      var idx = row.getAttribute('data-doc-index');
      if (saved[idx]) {
        var input = row.querySelector('input[type="checkbox"]');
        if (input) { input.checked = true; row.classList.add('done'); }
      }
    });
  }

  /* ── 9. Tabs (detal sahifa) ──────────────────────────────────────────── */
  function switchTab(tabName) {
    document.querySelectorAll('.gd-tab').forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-tab') === tabName);
    });
    document.querySelectorAll('.gd-tabpane').forEach(function (p) {
      p.classList.toggle('active', p.id === 'tab-' + tabName);
    });
  }
  window.switchTab = switchTab;

  /* ── init ────────────────────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', function () {
    var q = document.getElementById('gr-search');
    if (q) q.addEventListener('input', function () { searchGrants(q.value); });
    var country = document.getElementById('f-country');
    if (country) country.addEventListener('change', function () {
      filters.country = country.value; filters.page = 1; applyFilters();
    });
    var sort = document.getElementById('f-sort');
    if (sort) sort.addEventListener('change', function () {
      filters.sort = sort.value; filters.page = 1; applyFilters();
    });
    document.querySelectorAll('.gr-toggle-group').forEach(function (group) {
      var key = group.getAttribute('data-filter');
      group.querySelectorAll('.gr-toggle').forEach(function (btn) {
        btn.addEventListener('click', function () {
          group.querySelectorAll('.gr-toggle').forEach(function (b) { b.classList.remove('active'); });
          btn.classList.add('active');
          filters[key] = btn.getAttribute('data-value');
          filters.page = 1;
          applyFilters();
        });
      });
    });
    var clearBtn = document.getElementById('f-clear');
    if (clearBtn) clearBtn.addEventListener('click', clearFilters);
    if (S.pages) renderPagination(S.page || 1, S.pages, S.total || 0);
    restoreDocChecks();
    updateCountdowns();
    setInterval(updateCountdowns, 60000);
    checkDeadlineAlerts();
  });
})();
