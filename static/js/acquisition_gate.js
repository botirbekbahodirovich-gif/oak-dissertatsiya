/* Acquisition gate — mehmonlar (guest) uchun MAJBURIY "ro'yxatdan o'ting" modali.
 *
 * - 'show-login-modal' CustomEvent'ini tinglaydi ({detail:{total}}); total berilsa
 *   sarlavha "N ta natija mavjud" bo'ladi.
 * - [data-login-required] elementlaridagi bosishni ushlab, modalni chiqaradi.
 * - Global fetch 401 (login_required) javoblarини ushlab, modalni chiqaradi.
 *
 * Modal yopilmaydi (× / Esc / backdrop yo'q) — chiqish faqat ro'yxatdan o'tish
 * yoki kirish (navigatsiya) orqali. Markup: templates/components/login_required_modal.html.
 */
(function () {
  'use strict';

  function isGuest() { return window.OAK_LOGGED_IN === false; }

  var modal = null;
  function el(id) { return document.getElementById(id); }

  function openModal(total) {
    modal = modal || el('loginModal');
    if (!modal) return;
    var t = el('lm-title');
    if (t && typeof total === 'number' && total > 0) {
      t.textContent = total.toLocaleString('uz') + ' ta natija mavjud';
    }
    modal.classList.add('show');
    document.body.style.overflow = 'hidden';
  }

  // ── Public trigger: CustomEvent 'show-login-modal' ──
  window.addEventListener('show-login-modal', function (e) {
    if (!isGuest()) return;
    openModal(e.detail && e.detail.total);
  });

  document.addEventListener('DOMContentLoaded', function () {
    modal = el('loginModal');
    if (!modal) return;
    // Umumiy: [data-login-required] elementlar bosilganda modal (redirect emas)
    document.addEventListener('click', function (e) {
      if (!isGuest()) return;
      var trg = e.target.closest('[data-login-required]');
      if (!trg) return;
      e.preventDefault();
      e.stopPropagation();
      openModal();
    }, true);
  });

  // ── Modaldagi Telegram widjeti uchun callback (login.html'dagi bilan bir xil oqim) ──
  window.oakGateTgAuth = function (user) {
    if (window.releaseGuestFreeze) window.releaseGuestFreeze();
    var box = el('lm-tg-container');
    if (box) box.innerHTML = '<p style="color:#4a9eff;font-size:14px;margin:0">Tekshirilmoqda...</p>';
    fetch('/login/telegram', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(user)
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.success) { window.location.href = d.redirect || '/'; return; }
      var s = el('lm-tg-status');
      if (s) { s.textContent = d.error || 'Xatolik'; s.style.display = 'block'; }
    }).catch(function () {
      var s = el('lm-tg-status');
      if (s) { s.textContent = 'Tarmoq xatosi'; s.style.display = 'block'; }
    });
  };

  // ── Global 401 backstop: gated data endpointlari modalni chiqaradi ──
  var _fetch = window.fetch;
  window.fetch = function () {
    var p = _fetch.apply(this, arguments);
    return p.then(function (resp) {
      try {
        if (resp && resp.status === 401 && isGuest() &&
            (resp.headers.get('content-type') || '').indexOf('json') !== -1) {
          resp.clone().json().then(function (d) {
            if (d && d.error === 'login_required') {
              window.dispatchEvent(new CustomEvent('show-login-modal',
                { detail: { total: d.total } }));
            }
          }).catch(function () {});
        }
      } catch (err) { /* no-op */ }
      return resp;
    });
  };
})();
