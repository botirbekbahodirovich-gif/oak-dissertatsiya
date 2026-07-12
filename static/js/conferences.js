/* Konferensiyalar katalogi — mahalliy/xalqaro umumiy front mantiq.
   API: /api/v1/conferences (facet countlar bilan). Bookmark: dashboard
   yulduzcha UXi (user_conference_bookmarks). Mehmon → login tooltip. */
(function () {
  'use strict';
  var CFG = window.KF_CFG || {};
  var state = {
    time: 'upcoming', search: '', month: '', scopus: false, saved: false,
    field: [], region: [], type: [], publisher: [], format: [], page: 1
  };
  var savedIds = {};
  var seq = 0, debounceTimer = null;

  var grid = document.getElementById('kf-grid');
  var loading = document.getElementById('kf-loading');
  var empty = document.getElementById('kf-empty');
  var countEl = document.getElementById('kf-count');
  var pagEl = document.getElementById('kf-pagination');
  var clearBtn = document.getElementById('kf-clear');

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function hasFilters() {
    return !!(state.search || state.month || state.scopus || state.saved ||
      state.field.length || state.region.length || state.type.length ||
      state.publisher.length || state.format.length || state.time !== 'upcoming');
  }

  /* ── chips ── */
  function daysChip(c) {
    if (c.expired) return '<span class="kf-chip gray">O‘tgan</span>';
    var d = c.days_remaining;
    if (d == null) return '';
    if (d === 0) return '<span class="kf-chip r">Bugun</span>';
    var cls = d > 14 ? 'g' : (d >= 7 ? 'y' : 'r');
    return '<span class="kf-chip ' + cls + '">⏰ ' + d + ' kun qoldi</span>';
  }
  function cfpChip(c) {
    if (!c.submission_deadline) return '';
    var d = c.submission_days;
    if (d == null || d < 0) return '<span class="kf-chip gray">📝 CFP yopilgan</span>';
    var cls = d > 14 ? 'g' : (d >= 7 ? 'y' : 'r');
    return '<span class="kf-chip ' + cls + '">📝 Maqola: ' + d + ' kun</span>';
  }

  function card(c) {
    var star = savedIds[c.id]
      ? '<button class="kf-bm on" data-id="' + c.id + '" title="Saqlangan">★</button>'
      : '<button class="kf-bm" data-id="' + c.id + '" title="' +
        (CFG.loggedIn ? 'Saqlash' : 'Saqlash uchun tizimga kiring') + '">☆</button>';
    var badges = [];
    if (c.field) badges.push('<span class="kf-badge">' + esc(c.field) + '</span>');
    if (c.event_type) badges.push('<span class="kf-badge type">' + esc(c.event_type) + '</span>');
    if (c.publisher) badges.push('<span class="kf-badge pub">' + esc(c.publisher) + '</span>');
    if (c.is_scopus_indexed) badges.push('<span class="kf-badge scopus">✓ Scopus</span>');
    if (c.format_uz && CFG.scope === 'international')
      badges.push('<span class="kf-badge type">' + esc(c.format_uz) + '</span>');

    var when = c.start_uz || '';
    if (c.is_multiday && c.end_uz) when += ' – ' + c.end_uz;
    var place = [c.city, CFG.scope === 'local' ? c.region : c.country]
      .filter(function (x, i, a) { return x && a.indexOf(x) === i; }).join(', ');
    var meta = [];
    if (when) meta.push('📅 ' + esc(when) +
      (c.is_multiday ? ' <span style="color:#64748b;">(ko‘p kunlik)</span>' : ''));
    if (place) meta.push('📍 ' + esc(place));

    return '<div class="kf-card' + (c.expired ? ' expired' : '') + '">' + star +
      '<div class="kf-card-title"><a href="/konferensiya/' + esc(c.slug) + '">' +
        esc(c.title) + '</a></div>' +
      (c.organizer ? '<div class="kf-org">🏛️ ' + esc(c.organizer) + '</div>' : '') +
      (badges.length ? '<div class="kf-badges">' + badges.join('') + '</div>' : '') +
      (meta.length ? '<div class="kf-meta"><span>' + meta.join('</span><span>') + '</span></div>' : '') +
      '<div class="kf-foot"><span>' + daysChip(c) + ' ' + cfpChip(c) + '</span>' +
      '<a class="kf-more" href="/konferensiya/' + esc(c.slug) + '">Batafsil →</a></div>' +
    '</div>';
  }

  /* ── facetlar ── */
  var FACET_BOXES = { field: 'kf-f-field', region: 'kf-f-region',
                      type: 'kf-f-type', publisher: 'kf-f-publisher',
                      format: 'kf-f-format' };
  var FORMAT_UZ = { onsite: 'Anʼanaviy', online: 'Onlayn', hybrid: 'Gibrid' };

  function renderFacets(facets) {
    Object.keys(FACET_BOXES).forEach(function (dim) {
      var box = document.getElementById(FACET_BOXES[dim]);
      if (!box) return;
      var opts = (facets && facets[dim]) || [];
      if (!opts.length && !state[dim].length) {
        box.innerHTML = '<div style="color:#64748b;font-size:0.8rem;">—</div>';
        return;
      }
      // tanlangan, lekin joriy natijada 0 bo'lganlar ham ko'rinib tursin
      var seen = {};
      opts.forEach(function (o) { seen[o.value] = true; });
      state[dim].forEach(function (v) {
        if (!seen[v]) opts.push({ value: v, count: 0 });
      });
      box.innerHTML = opts.slice(0, 14).map(function (o) {
        var checked = state[dim].indexOf(o.value) !== -1;
        var label = dim === 'format' ? (FORMAT_UZ[o.value] || o.value) : o.value;
        return '<label class="kf-fopt"><input type="checkbox" data-dim="' + dim +
          '" value="' + esc(o.value) + '"' + (checked ? ' checked' : '') + '>' +
          '<span class="lbl" title="' + esc(label) + '">' + esc(label) + '</span>' +
          '<span class="n">' + o.count + '</span></label>';
      }).join('');
    });
  }

  /* ── yuklash ── */
  function load() {
    var p = new URLSearchParams();
    p.set('scope', CFG.scope);
    p.set('time', state.time);
    p.set('page', state.page);
    if (state.search) p.set('search', state.search);
    if (state.month) p.set('month', state.month);
    if (state.scopus) p.set('scopus', '1');
    if (state.saved) p.set('saved', '1');
    ['field', 'region', 'type', 'publisher', 'format'].forEach(function (dim) {
      state[dim].forEach(function (v) { p.append(dim, v); });
    });
    var mySeq = ++seq;
    loading.style.display = '';
    grid.style.display = 'none';
    empty.style.display = 'none';

    fetch('/api/v1/conferences?' + p.toString(), { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (mySeq !== seq) return;
        loading.style.display = 'none';
        if (!d.ok) { empty.style.display = ''; return; }
        savedIds = {};
        (d.saved_ids || []).forEach(function (id) { savedIds[id] = true; });
        if (d.items.length) {
          grid.innerHTML = d.items.map(card).join('');
          grid.style.display = '';
        } else {
          empty.style.display = '';
        }
        countEl.textContent = d.count + ' ta topildi' +
          (d.pages > 1 ? ' · ' + d.page + '/' + d.pages + '-sahifa' : '');
        renderFacets(d.facets);
        renderPagination(d.page, d.pages);
        clearBtn.style.display = hasFilters() ? '' : 'none';
        bindCards();
      })
      .catch(function () {
        if (mySeq !== seq) return;
        loading.style.display = 'none';
        empty.style.display = '';
      });
  }

  function renderPagination(page, pages) {
    if (pages <= 1) { pagEl.innerHTML = ''; return; }
    var html = '';
    var lo = Math.max(1, page - 2), hi = Math.min(pages, page + 2);
    if (lo > 1) html += pageBtn(1, page) + (lo > 2 ? '<span style="color:#64748b;">…</span>' : '');
    for (var i = lo; i <= hi; i++) html += pageBtn(i, page);
    if (hi < pages) html += (hi < pages - 1 ? '<span style="color:#64748b;">…</span>' : '') + pageBtn(pages, page);
    pagEl.innerHTML = html;
    pagEl.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () {
        state.page = parseInt(b.getAttribute('data-p'), 10);
        load();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      });
    });
  }
  function pageBtn(n, cur) {
    return '<button class="kf-page-btn' + (n === cur ? ' active' : '') +
      '" data-p="' + n + '">' + n + '</button>';
  }

  /* ── bookmark ── */
  function bindCards() {
    grid.querySelectorAll('.kf-bm').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!CFG.loggedIn) {
          window.location.href = '/login?next=' + encodeURIComponent(location.pathname);
          return;
        }
        var id = parseInt(btn.getAttribute('data-id'), 10);
        fetch('/api/v1/conferences/bookmark', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ conference_id: id })
        }).then(function (r) { return r.json(); }).then(function (d) {
          if (!d.success) return;
          savedIds[id] = d.saved;
          btn.classList.toggle('on', d.saved);
          btn.textContent = d.saved ? '★' : '☆';
          btn.title = d.saved ? 'Saqlangan' : 'Saqlash';
          if (state.saved && !d.saved) load(); // Saqlanganlar rejimida o'chirilsa yo'qoladi
        }).catch(function () {});
      });
    });
  }

  /* ── boshqaruvlar ── */
  document.getElementById('kf-time').querySelectorAll('.kf-toggle').forEach(function (b) {
    b.addEventListener('click', function () {
      document.getElementById('kf-time').querySelectorAll('.kf-toggle')
        .forEach(function (x) { x.classList.toggle('active', x === b); });
      state.time = b.getAttribute('data-value');
      state.page = 1;
      load();
    });
  });
  var savedBtn = document.getElementById('kf-saved');
  if (savedBtn) savedBtn.addEventListener('click', function () {
    state.saved = !state.saved;
    savedBtn.classList.toggle('active', state.saved);
    state.page = 1;
    load();
  });
  var searchEl = document.getElementById('kf-search');
  searchEl.addEventListener('input', function () {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () {
      state.search = searchEl.value.trim();
      state.page = 1;
      load();
    }, 300); /* debounce — spec */
  });
  document.getElementById('kf-month').addEventListener('change', function (e) {
    state.month = e.target.value;
    state.page = 1;
    load();
  });
  var scopusEl = document.getElementById('kf-scopus');
  if (scopusEl) scopusEl.addEventListener('change', function () {
    state.scopus = scopusEl.checked;
    state.page = 1;
    load();
  });
  /* facet checkboxlar — delegatsiya (har renderda qayta bog'lamaslik uchun) */
  document.getElementById('kf-side').addEventListener('change', function (e) {
    var t = e.target;
    if (!t.matches('input[type="checkbox"][data-dim]')) return;
    var dim = t.getAttribute('data-dim'), v = t.value;
    var idx = state[dim].indexOf(v);
    if (t.checked && idx === -1) state[dim].push(v);
    if (!t.checked && idx !== -1) state[dim].splice(idx, 1);
    state.page = 1;
    load();
  });
  clearBtn.addEventListener('click', function () {
    state = { time: 'upcoming', search: '', month: '', scopus: false, saved: false,
              field: [], region: [], type: [], publisher: [], format: [], page: 1 };
    searchEl.value = '';
    document.getElementById('kf-month').value = '';
    if (scopusEl) scopusEl.checked = false;
    if (savedBtn) savedBtn.classList.remove('active');
    document.getElementById('kf-time').querySelectorAll('.kf-toggle').forEach(function (x) {
      x.classList.toggle('active', x.getAttribute('data-value') === 'upcoming');
    });
    load();
  });
  var sideToggle = document.getElementById('kf-side-toggle');
  if (sideToggle) sideToggle.addEventListener('click', function () {
    document.getElementById('kf-side').classList.toggle('open');
  });

  /* ── hero count-up (bosh sahifa naqshi) ── */
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduced) {
    document.querySelectorAll('.kf-stats b[data-count]').forEach(function (el) {
      var target = parseInt(el.getAttribute('data-count'), 10) || 0;
      if (!target) return;
      var t0 = null, dur = 1200;
      function frame(ts) {
        if (!t0) t0 = ts;
        var p = Math.min((ts - t0) / dur, 1);
        el.textContent = Math.round((1 - Math.pow(2, -10 * p)) * target);
        if (p < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }

  /* URL dan boshlang'ich filtr (?field=… — obuna banneri/tashqi havolalar) */
  var q0 = new URLSearchParams(location.search);
  if (q0.get('search')) { state.search = q0.get('search'); searchEl.value = state.search; }
  if (q0.get('field')) state.field = q0.getAll('field');

  load();
})();
