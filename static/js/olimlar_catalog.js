/* Olimlar katalogi 2.0 — filtr, dual view, follow, ratings. Vanilla JS. */
(function () {
  'use strict';
  var IS_AUTH = window.OC_AUTH === true;
  var state = { q: '', ixtisoslik: [], viloyat: [], daraja: '', faollik: '',
                orcid: '', claimed: '', sort: 'students', view: '', page: 1 };
  var els = {};
  var openGroups = {};   // guruh kaliti -> nechinchi sahifagacha yuklangan

  function $(id) { return document.getElementById(id); }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function fmt(n) { return (n || 0).toLocaleString('ru-RU').replace(/,/g, ' '); }

  // ── URL state ──
  function toURL() {
    var p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (state.ixtisoslik.length) p.set('ixtisoslik', state.ixtisoslik.join(','));
    if (state.viloyat.length) p.set('viloyat', state.viloyat.join(','));
    ['daraja', 'faollik', 'orcid', 'claimed'].forEach(function (k) { if (state[k]) p.set(k, state[k]); });
    if (state.sort !== 'students') p.set('sort', state.sort);
    if (state.view) p.set('view', state.view);
    if (state.page > 1) p.set('page', state.page);
    return p.toString();
  }
  function fromURL() {
    var p = new URLSearchParams(location.search);
    state.q = p.get('q') || '';
    state.ixtisoslik = (p.get('ixtisoslik') || '').split(',').filter(Boolean);
    state.viloyat = (p.get('viloyat') || '').split(',').filter(Boolean);
    state.daraja = p.get('daraja') || '';
    state.faollik = p.get('faollik') || '';
    state.orcid = p.get('orcid') || '';
    state.claimed = p.get('claimed') || '';
    state.sort = p.get('sort') || 'students';
    state.view = p.get('view') || localStorage.getItem('oc_view') || '';
    state.page = parseInt(p.get('page'), 10) || 1;
  }
  function syncURL() {
    try { history.replaceState(null, '', location.pathname + (toURL() ? '?' + toURL() : '')); } catch (e) {}
  }

  // ── API params ──
  function apiParams() {
    var p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (state.ixtisoslik.length) p.set('ixtisoslik', state.ixtisoslik[0]); // server 1 ta shifrni oladi
    if (state.viloyat.length) p.set('viloyat', state.viloyat[0]);
    ['daraja', 'faollik', 'orcid', 'claimed'].forEach(function (k) { if (state[k]) p.set(k, state[k]); });
    p.set('sort', state.sort);
    if (state.view) p.set('view', state.view);
    p.set('page', state.page);
    return p;
  }

  // ── avatar ──
  var COLORS = ['#3b82f6', '#8b5cf6', '#059669', '#e36403', '#0ea5e9', '#d946ef', '#f43f5e', '#14b8a6'];
  function avatar(s) {
    if (s.photo_url) return '<img class="oc-av" src="' + esc(s.photo_url) + '" alt="' + esc(s.display) +
      '" loading="lazy" onerror="this.replaceWith(document.createRange().createContextualFragment(window.ocInitial(' +
      JSON.stringify(JSON.stringify(s.display)) + ')))">';
    return ocInitial(s.display);
  }
  window.ocInitial = function (name) {
    try { name = JSON.parse(name); } catch (e) {}
    var ch = (name || '?').trim()[0] || '?';
    var h = 0; for (var i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h);
    var c = COLORS[Math.abs(h) % COLORS.length];
    return '<div class="oc-av" style="background:' + c + '">' + esc(ch.toUpperCase()) + '</div>';
  };

  // ── card ──
  function card(s) {
    var badges = '';
    if (s.dsc_students) badges += '<span class="oc-b dsc">DSc</span>';
    else if (s.degree && /dsc|док/i.test(s.degree)) badges += '<span class="oc-b dsc">DSc</span>';
    if (s.phd_students) badges += '<span class="oc-b phd">PhD</span>';
    if (s.has_orcid) badges += '<span class="oc-b orcid">✓ORCID</span>';
    if (s.is_claimed) badges += '<span class="oc-b claimed">✓ Tasdiqlangan</span>';
    if (s.has_google_scholar) badges += '<a class="oc-b gs" href="' + esc(s.google_scholar_url) +
      '" target="_blank" rel="noopener" title="Google Scholar">🎓</a>';
    var inst = s.institutions && s.institutions.length ? '📍 ' + esc(s.institutions.join(', ')) : '';
    var spec = s.specialties && s.specialties.length ? '🔬 ' + esc(s.specialties.join(', ')) : '';
    var yrs = (s.first_year && s.last_year) ? s.first_year + '–' + s.last_year : '';
    var followBtn = IS_AUTH
      ? '<button class="oc-btn follow' + (s.is_following ? ' on' : '') + '" data-follow="' + esc(s.slug) +
        '" data-name="' + esc(s.name) + '">' + (s.is_following ? '🔔 Kuzatilyapti' : '🔔 Kuzatish') + '</button>'
      : '<button class="oc-btn follow" title="Kuzatish uchun tizimga kiring" onclick="location.href=\'/login\'">🔔 Kuzatish</button>';
    return '<div class="oc-card">' +
      '<div class="oc-card-top">' + avatar(s) + '<div class="oc-card-hd">' +
        '<div class="oc-card-name">' + esc(s.display) + '</div>' +
        '<div class="oc-badges">' + badges + '</div></div></div>' +
      '<div class="oc-card-meta">' + (inst ? '<span>' + inst + '</span>' : '') + (spec ? '<span>' + spec + '</span>' : '') + '</div>' +
      '<div class="oc-stats">' +
        '<div class="oc-stat"><b>' + s.total_students + '</b><span>👥 shogird</span>' +
          '<small>PhD:' + s.phd_students + ' DSc:' + s.dsc_students + '</small></div>' +
        '<div class="oc-stat"><b>' + s.opponent_count + '</b><span>⚖️ opponent</span></div>' +
        '<div class="oc-stat"><b>' + s.next_gen_advisors + '</b><span>🌳 avlod</span></div>' +
        '<div class="oc-stat"><b>' + s.publications_count + '</b><span>📄 nashr</span></div></div>' +
      (s.sparkline ? '<div class="oc-spark"><div class="oc-spark-lbl">Faollik' + (yrs ? ' (' + yrs + ')' : '') + '</div>' + s.sparkline + '</div>' : '') +
      '<div class="oc-card-acts">' +
        '<a class="oc-btn primary" href="/olim/' + encodeURIComponent(s.name) + '">Profil</a>' + followBtn + '</div>' +
      '</div>';
  }

  // ── render: cards ──
  function renderCards(d) {
    if (!d.scholars.length) { els.content.innerHTML = emptyState(); return; }
    els.content.innerHTML = '<div class="oc-grid">' + d.scholars.map(card).join('') + '</div>';
    bindFollows(els.content);
  }

  // ── render: grouped ──
  function renderGroups(d) {
    if (!d.groups.length) { els.content.innerHTML = emptyState(); return; }
    var unit = groupUnit(d.group_key === 'total_students' ? 'students' : d.sort);
    var maxCnt = d.groups.reduce(function (m, g) { return Math.max(m, g.scholar_count); }, 1);
    var html = d.groups.map(function (g, gi) {
      var w = Math.max(8, Math.round(g.scholar_count / maxCnt * 100));
      var open = gi === 0;   // birinchi guruh ochiq
      return '<div class="oc-group' + (open ? ' open' : '') + '" data-gkey="' + g.key + '">' +
        '<div class="oc-group-row" data-toggle="' + g.key + '">' +
          '<span class="oc-group-bar" style="width:' + w + '%"></span>' +
          '<span class="oc-group-badge">' + g.key + ' ta ' + unit + '</span>' +
          '<span class="oc-group-mid"></span>' +
          '<span class="oc-group-cnt">' + g.scholar_count + ' ta olim</span>' +
          '<span class="oc-group-arr">▶</span></div>' +
        '<div class="oc-group-body"><div class="oc-group-grid">' + g.scholars.map(card).join('') + '</div>' +
          (g.has_more ? '<button class="oc-more" data-more="' + g.key + '" data-gpage="2">Yana ' +
            (g.total_in_group - g.scholars.length) + ' ta →</button>' : '') +
        '</div></div>';
    }).join('');
    els.content.innerHTML = html;
    bindGroups();
    bindFollows(els.content);
  }
  function groupUnit(sort) {
    return { students: 'shogird', opponents: 'opponentlik', generations: 'avlod',
             publications: 'nashr' }[sort] || 'shogird';
  }

  function emptyState() {
    return '<div class="oc-empty"><h3>Mos olim topilmadi</h3>' +
      '<p>Ushbu parametrlarga mos olim yo\'q. Filtrlarni o\'zgartiring.</p>' +
      '<button class="oc-btn primary" style="max-width:200px;margin:12px auto 0" onclick="window.ocClear()">Tozalash</button></div>';
  }

  // ── group interactions ──
  function bindGroups() {
    els.content.querySelectorAll('.oc-group-row').forEach(function (row) {
      row.addEventListener('click', function () { row.closest('.oc-group').classList.toggle('open'); });
    });
    els.content.querySelectorAll('.oc-more').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var key = btn.getAttribute('data-more'), gpage = parseInt(btn.getAttribute('data-gpage'), 10);
        var p = apiParams(); p.set('key', key); p.set('gpage', gpage); p.delete('view'); p.delete('page');
        btn.disabled = true; btn.textContent = 'Yuklanmoqda…';
        fetch('/api/olimlar/group?' + p.toString()).then(function (r) { return r.json(); }).then(function (d) {
          if (!d.ok) return;
          var grid = btn.parentElement.querySelector('.oc-group-grid');
          grid.insertAdjacentHTML('beforeend', d.scholars.map(card).join(''));
          bindFollows(grid);
          if (d.has_more) { btn.disabled = false; btn.setAttribute('data-gpage', gpage + 1); btn.textContent = 'Yana yuklash →'; }
          else btn.remove();
        });
      });
    });
  }

  // ── follow ──
  function bindFollows(root) {
    root.querySelectorAll('[data-follow]').forEach(function (btn) {
      if (btn._bound) return; btn._bound = true;
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var slug = btn.getAttribute('data-follow');
        var fd = new FormData(); fd.append('name', btn.getAttribute('data-name'));
        fetch('/api/olimlar/' + encodeURIComponent(slug) + '/follow', { method: 'POST', body: fd })
          .then(function (r) { return r.json(); }).then(function (d) {
            if (!d.ok) return;
            btn.classList.toggle('on', d.following);
            btn.textContent = d.following ? '🔔 Kuzatilyapti' : '🔔 Kuzatish';
          });
      });
    });
  }

  // ── load ──
  var seq = 0;
  function load() {
    var my = ++seq;
    els.content.innerHTML = '<div class="oc-loading"><span class="oc-spin"></span>Yuklanmoqda…</div>';
    syncURL();
    fetch('/api/olimlar?' + apiParams().toString()).then(function (r) { return r.json(); }).then(function (d) {
      if (my !== seq || !d.ok) return;
      $('oc-total').textContent = fmt(d.total);
      setActiveView(d.view);
      if (d.view === 'grouped') renderGroups(d); else renderCards(d);
      renderPager(d.page, d.pages);
      renderChips();
    }).catch(function () { if (my === seq) els.content.innerHTML = emptyState(); });
  }

  function setActiveView(view) {
    $('oc-v-grouped').classList.toggle('active', view === 'grouped');
    $('oc-v-cards').classList.toggle('active', view === 'cards');
  }

  // ── pager ──
  function renderPager(page, pages) {
    if (pages <= 1) { els.pager.innerHTML = ''; return; }
    var h = '';
    h += '<button ' + (page <= 1 ? 'disabled' : '') + ' data-pg="' + (page - 1) + '">‹</button>';
    var from = Math.max(1, page - 2), to = Math.min(pages, page + 2);
    if (from > 1) h += '<button data-pg="1">1</button>' + (from > 2 ? '<span class="oc-muted">…</span>' : '');
    for (var i = from; i <= to; i++) h += '<button class="' + (i === page ? 'active' : '') + '" data-pg="' + i + '">' + i + '</button>';
    if (to < pages) h += (to < pages - 1 ? '<span class="oc-muted">…</span>' : '') + '<button data-pg="' + pages + '">' + pages + '</button>';
    h += '<button ' + (page >= pages ? 'disabled' : '') + ' data-pg="' + (page + 1) + '">›</button>';
    els.pager.innerHTML = h;
    els.pager.querySelectorAll('[data-pg]').forEach(function (b) {
      b.addEventListener('click', function () { state.page = parseInt(b.getAttribute('data-pg'), 10); load(); window.scrollTo({ top: 0, behavior: 'smooth' }); });
    });
  }

  // ── chips ──
  function renderChips() {
    var chips = [];
    state.ixtisoslik.forEach(function (v) { chips.push(['ixtisoslik', v, v]); });
    state.viloyat.forEach(function (v) { chips.push(['viloyat', v, v]); });
    if (state.daraja) chips.push(['daraja', state.daraja, state.daraja.toUpperCase()]);
    if (state.faollik) chips.push(['faollik', state.faollik, 'Oxirgi ' + state.faollik + ' yil']);
    if (state.orcid) chips.push(['orcid', '1', 'ORCID']);
    if (state.claimed) chips.push(['claimed', '1', 'Tasdiqlangan']);
    els.chips.innerHTML = chips.map(function (c) {
      return '<span class="oc-chip">' + esc(c[2]) + '<button data-rm="' + c[0] + '" data-val="' + esc(c[1]) + '">✕</button></span>';
    }).join('');
    els.chips.querySelectorAll('[data-rm]').forEach(function (b) {
      b.addEventListener('click', function () { removeFilter(b.getAttribute('data-rm'), b.getAttribute('data-val')); });
    });
    $('oc-filter-badge').textContent = chips.length ? '(' + chips.length + ')' : '';
  }
  function removeFilter(k, v) {
    if (k === 'ixtisoslik' || k === 'viloyat') {
      state[k] = state[k].filter(function (x) { return x !== v; });
      els.sidebar.querySelectorAll('input[name=' + k + ']').forEach(function (i) { if (i.value === v) i.checked = false; });
    } else {
      state[k] = '';
      els.sidebar.querySelectorAll('input[name=' + k + ']').forEach(function (i) { if (i.value === v) i.checked = false; });
      if (k === 'daraja' || k === 'faollik') { var d = els.sidebar.querySelector('input[name=' + k + '][value=""]'); if (d) d.checked = true; }
    }
    state.page = 1; load();
  }

  // ── ratings ──
  var ratingsData = null;
  function loadRatings() {
    fetch('/api/olimlar/ratings').then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return; ratingsData = d; renderRTab('top_students');
    });
  }
  function renderRTab(tab) {
    if (!ratingsData) return;
    var body = $('oc-rtab-body');
    if (tab === 'regions') {
      body.innerHTML = ratingsData.regions.map(function (r) {
        return '<div class="oc-rreg"><b>' + esc(r.region) + '</b>' + r.scholars.map(function (s, i) {
          return '<div class="oc-rrow"><span class="n">' + (i + 1) + '</span><a href="/olim/' + encodeURIComponent(s.name) + '">' +
            esc(s.display) + '</a><span class="v">' + s.total_students + '</span></div>'; }).join('') + '</div>';
      }).join('');
    } else {
      var list = ratingsData[tab] || [];
      body.innerHTML = list.map(function (s, i) {
        return '<div class="oc-rrow"><span class="n">' + (i + 1) + '</span><a href="/olim/' + encodeURIComponent(s.name) + '">' +
          esc(s.display) + '</a><span class="v">' + s.total_students + ' 👥</span></div>';
      }).join('') || '<div class="oc-muted">Ma\'lumot yo\'q</div>';
    }
  }

  // ── init ──
  window.ocClear = function () {
    state.q = ''; state.ixtisoslik = []; state.viloyat = []; state.daraja = '';
    state.faollik = ''; state.orcid = ''; state.claimed = ''; state.page = 1;
    els.q.value = '';
    els.sidebar.querySelectorAll('input[type=checkbox]').forEach(function (i) { i.checked = false; });
    els.sidebar.querySelectorAll('input[value=""]').forEach(function (i) { i.checked = true; });
    load();
  };

  function readSidebar() {
    state.ixtisoslik = []; state.viloyat = [];
    els.sidebar.querySelectorAll('input[name=ixtisoslik]:checked').forEach(function (i) { state.ixtisoslik.push(i.value); });
    els.sidebar.querySelectorAll('input[name=viloyat]:checked').forEach(function (i) { state.viloyat.push(i.value); });
    var dr = els.sidebar.querySelector('input[name=daraja]:checked'); state.daraja = dr ? dr.value : '';
    var fa = els.sidebar.querySelector('input[name=faollik]:checked'); state.faollik = fa ? fa.value : '';
    state.orcid = $('oc-orcid').checked ? '1' : '';
    state.claimed = $('oc-claimed').checked ? '1' : '';
    state.page = 1; load();
  }

  function applyStateToInputs() {
    els.q.value = state.q;
    state.ixtisoslik.forEach(function (v) { var i = els.sidebar.querySelector('input[name=ixtisoslik][value="' + v + '"]'); if (i) i.checked = true; });
    state.viloyat.forEach(function (v) { var i = els.sidebar.querySelector('input[name=viloyat][value="' + CSS.escape(v) + '"]'); if (i) i.checked = true; });
    if (state.daraja) { var d = els.sidebar.querySelector('input[name=daraja][value="' + state.daraja + '"]'); if (d) d.checked = true; }
    if (state.faollik) { var f = els.sidebar.querySelector('input[name=faollik][value="' + state.faollik + '"]'); if (f) f.checked = true; }
    if (state.orcid) $('oc-orcid').checked = true;
    if (state.claimed) $('oc-claimed').checked = true;
    els.sort.value = state.sort;
  }

  function init() {
    els = { q: $('oc-q'), sidebar: $('oc-sidebar'), content: $('oc-content'),
            pager: $('oc-pager'), chips: $('oc-chips'), sort: $('oc-sort') };
    fromURL();
    applyStateToInputs();

    var qt;
    els.q.addEventListener('input', function () { clearTimeout(qt); qt = setTimeout(function () { state.q = els.q.value.trim(); state.page = 1; load(); }, 300); });
    els.sidebar.addEventListener('change', readSidebar);
    els.sort.addEventListener('change', function () { state.sort = els.sort.value; state.page = 1; load(); });
    $('oc-clear').addEventListener('click', window.ocClear);

    // view toggle
    document.querySelectorAll('.oc-vtoggle button').forEach(function (b) {
      b.addEventListener('click', function () { state.view = b.getAttribute('data-view'); localStorage.setItem('oc_view', state.view); state.page = 1; load(); });
    });
    $('oc-copy').addEventListener('click', function () {
      navigator.clipboard.writeText(location.href).then(function () { $('oc-copy').textContent = '✓'; setTimeout(function () { $('oc-copy').textContent = '🔗'; }, 1200); });
    });

    // ixtisoslik facet search
    var ixs = $('oc-ixt-search');
    if (ixs) ixs.addEventListener('input', function () {
      var v = ixs.value.toLowerCase();
      $('oc-facet-ixt').querySelectorAll('.oc-check').forEach(function (l) {
        l.style.display = l.textContent.toLowerCase().indexOf(v) >= 0 ? '' : 'none';
      });
    });

    // ratings collapse + tabs
    var rToggle = $('oc-ratings-toggle');
    if (localStorage.getItem('oc_ratings') === 'closed') $('oc-ratings').classList.add('collapsed');
    rToggle.addEventListener('click', function () {
      var col = $('oc-ratings').classList.toggle('collapsed');
      localStorage.setItem('oc_ratings', col ? 'closed' : 'open');
    });
    document.querySelectorAll('.oc-rtab').forEach(function (t) {
      t.addEventListener('click', function () {
        document.querySelectorAll('.oc-rtab').forEach(function (x) { x.classList.remove('active'); });
        t.classList.add('active'); renderRTab(t.getAttribute('data-rtab'));
      });
    });

    // mobile drawer
    var mob = $('oc-mob-filter'), bd = $('oc-backdrop');
    function closeDrawer() { els.sidebar.classList.remove('open'); bd.classList.remove('show'); }
    if (mob) mob.addEventListener('click', function () { els.sidebar.classList.add('open'); bd.classList.add('show'); });
    $('oc-side-close').addEventListener('click', closeDrawer);
    bd.addEventListener('click', closeDrawer);

    load();
    loadRatings();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
