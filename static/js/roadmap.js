/* Akademik reja (roadmap) dashboard — tab almashish + CRUD (fetch JSON).
   Server API: blueprints/roadmap.py (/api/reja/*). Muvaffaqiyatli yozuvdan
   keyin sahifa yangilanadi (server-render'ga qaytish — holat sinxron). */
(function () {
  'use strict';

  // ── tabs ──
  var tabs = document.querySelectorAll('.rj-tab');
  tabs.forEach(function (btn) {
    btn.addEventListener('click', function () { activate(btn.getAttribute('data-tab')); });
  });
  function activate(name) {
    tabs.forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-tab') === name); });
    document.querySelectorAll('.rj-pane').forEach(function (p) {
      p.classList.toggle('active', p.id === 'pane-' + name);
    });
    if (history.replaceState) history.replaceState(null, '', '#' + name);
  }
  // URL hash orqali to'g'ridan-to'g'ri tab ochish (deep-link)
  if (location.hash) {
    var h = location.hash.slice(1);
    if (document.getElementById('pane-' + h)) activate(h);
  }
  window.rjGoto = activate;

  // ── umumiy yordamchilar ──
  function post(url, body) {
    return fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }
  function reloadOr(d) {
    if (d && d.success) { location.reload(); }
    else { alert((d && d.error) || 'Xatolik yuz berdi'); }
  }
  function val(id) { var el = document.getElementById(id); return el ? el.value.trim() : ''; }

  window.rjPost = function (url) { post(url).then(reloadOr); };
  window.rjToggleForm = function (id) {
    var f = document.getElementById(id);
    if (f) f.classList.toggle('open');
  };

  // ── nashrlar ──
  window.rjAddPub = function () {
    if (!val('pub-title')) { alert('Sarlavha kiritilishi shart'); return; }
    post('/api/reja/pub/add', {
      title: val('pub-title'), pub_type: val('pub-type'), status: val('pub-status'),
      venue: val('pub-venue'), year: val('pub-year'), url: val('pub-url')
    }).then(reloadOr);
  };
  window.rjPubStatus = function (id, status) {
    post('/api/reja/pub/' + id + '/update', { status: status }).then(function (d) {
      if (!d.success) { alert(d.error || 'Xatolik'); } else { location.reload(); }
    });
  };

  // ── uchrashuvlar ──
  window.rjAddMeeting = function () {
    if (!val('meet-title')) { alert('Mavzu kiritilishi shart'); return; }
    post('/api/reja/meeting/add', {
      title: val('meet-title'), meeting_date: val('meet-date'), notes: val('meet-notes')
    }).then(reloadOr);
  };

  // ── konferensiyalar ──
  window.rjAddConf = function () {
    if (!val('conf-name')) { alert('Nomi kiritilishi shart'); return; }
    post('/api/reja/conf/add', {
      name: val('conf-name'), location: val('conf-location'), url: val('conf-url'),
      event_date: val('conf-date'), deadline: val('conf-deadline')
    }).then(reloadOr);
  };
  window.rjConfStatus = function (id, status) {
    post('/api/reja/conf/' + id + '/status', { status: status }).then(function (d) {
      if (!d.success) { alert(d.error || 'Xatolik'); } else { location.reload(); }
    });
  };

  // ── o'chirish (umumiy) ──
  var DEL_URLS = {
    pub: '/api/reja/pub/{id}/delete',
    meeting: '/api/reja/meeting/{id}/delete',
    conf: '/api/reja/conf/{id}/delete'
  };
  window.rjDel = function (kind, id) {
    if (!confirm("O'chirishga ishonchingiz komilmi?")) return;
    post(DEL_URLS[kind].replace('{id}', id)).then(reloadOr);
  };
})();
