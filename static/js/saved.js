/* saved.js — dissertatsiyalarni saqlash (bookmark) tugmalari.
 *
 * Yagona manba: server tomonda user_bookmarks jadvali (/api/saved/*), ya'ni
 * dashboard yulduzchasi bilan sinxron. Har qanday
 *   <button class="js-save-btn" data-dissertation-id="123">…</button>
 * elementini avtomatik boyitadi (ixtiyoriy ichki .save-ico / .save-txt).
 *
 * Login holati: window.SAVED_LOGGED_IN (sahifada o'rnatiladi).
 * Kabinet nav badge: #saved-nav-count.
 */
(function () {
  'use strict';

  var SVG_OUTLINE =
    '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" ' +
    'stroke-width="1.6" stroke="currentColor" width="18" height="18" aria-hidden="true">' +
    '<path stroke-linecap="round" stroke-linejoin="round" d="M17.593 3.322c1.1.128 ' +
    '1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 ' +
    '48.507 0 0 1 11.186 0Z"/></svg>';

  var SVG_FILLED =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" ' +
    'width="18" height="18" aria-hidden="true"><path fill-rule="evenodd" d="M6.32 2.577a49.255 ' +
    '49.255 0 0 1 11.36 0c1.497.174 2.57 1.46 2.57 2.93V21a.75.75 0 0 1-1.085.67L12 ' +
    '18.089l-7.165 3.583A.75.75 0 0 1 3.75 21V5.507c0-1.47 1.073-2.756 2.57-2.93Z" ' +
    'clip-rule="evenodd"/></svg>';

  var savedSet = new Set();

  function paint(btn, isSaved) {
    btn.classList.toggle('saved', isSaved);
    btn.setAttribute('aria-pressed', isSaved ? 'true' : 'false');
    btn.title = isSaved ? 'Saqlangan — olib tashlash' : 'Saqlash';
    var ico = btn.querySelector('.save-ico');
    if (ico) {
      ico.innerHTML = isSaved ? SVG_FILLED : SVG_OUTLINE;
    } else if (!btn.querySelector('.save-txt')) {
      btn.innerHTML = isSaved ? SVG_FILLED : SVG_OUTLINE;   // faqat-ikonli tugma
    }
    var txt = btn.querySelector('.save-txt');
    if (txt) txt.textContent = isSaved ? 'Saqlangan' : 'Saqlash';
  }

  function setBadge(n) {
    var b = document.getElementById('saved-nav-count');
    if (!b) return;
    if (n > 0) { b.textContent = n; b.style.display = ''; b.hidden = false; }
    else { b.style.display = 'none'; b.hidden = true; }
  }

  function idOf(btn) {
    return parseInt(btn.getAttribute('data-dissertation-id'), 10);
  }

  function handleGuest() {
    // Ilova global login modalni qo'llasa — u hodisani preventDefault qiladi.
    var ev = new CustomEvent('show-login-modal', {
      detail: { reason: 'save' }, cancelable: true, bubbles: true
    });
    var notCanceled = document.dispatchEvent(ev);
    if (notCanceled && !ev.defaultPrevented) {
      if (confirm("Saqlash uchun tizimga kirish kerak. Kirish sahifasiga o'tilsinmi?"))
        window.location.href = '/login?next=' +
          encodeURIComponent(location.pathname + location.search);
    }
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('.js-save-btn') : null;
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var id = idOf(btn);
    if (!id) return;
    if (!window.SAVED_LOGGED_IN) { handleGuest(); return; }

    var wasSaved = btn.classList.contains('saved');
    paint(btn, !wasSaved);                 // optimistik
    btn.disabled = true;
    fetch('/api/saved/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dissertation_id: id })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.success) { paint(btn, wasSaved); return; }
      paint(btn, d.saved);
      if (d.saved) savedSet.add(id); else savedSet.delete(id);
      if (typeof d.total_saved === 'number') setBadge(d.total_saved);
    }).catch(function () {
      paint(btn, wasSaved);                // xato — holatni qaytar
    }).finally(function () { btn.disabled = false; });
  }, true);

  function init() {
    var btns = document.querySelectorAll('.js-save-btn');
    for (var i = 0; i < btns.length; i++) paint(btns[i], false);  // default: outline
    if (!window.SAVED_LOGGED_IN) return;
    fetch('/api/saved/ids').then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.success) return;
      savedSet = new Set(d.ids || []);
      var els = document.querySelectorAll('.js-save-btn');
      for (var i = 0; i < els.length; i++) paint(els[i], savedSet.has(idOf(els[i])));
      setBadge(typeof d.count === 'number' ? d.count : savedSet.size);
    }).catch(function () {});
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', init);
  else
    init();
})();
