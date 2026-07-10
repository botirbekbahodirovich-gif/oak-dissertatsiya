/* Acquisition gate — mehmonlar (guest) uchun "ro'yxatdan o'ting" modali.
 *
 * - 'show-login-modal' CustomEvent'ini tinglaydi ({detail:{reason, total}}).
 * - [data-login-required] elementlaridagi bosishni ushlab, modalni chiqaradi.
 * - Global fetch 401 (login_required) javoblarини ushlab, modalni chiqaradi
 *   (masalan /data yoki /api/dashboard/search — sahifa > 1 / filter uchun).
 *
 * Modal markupi: templates/components/login_required_modal.html (base.html include).
 */
(function () {
  'use strict';

  function isGuest() { return window.OAK_LOGGED_IN === false; }

  // reason → modal matni (o'zbekcha)
  var REASONS = {
    filter:     { emoji: '🔍', title: 'Filtrlash uchun ro\'yxatdan o\'ting',
                  sub: 'Natijalarni filtrlash va saralash bepul hisob bilan ochiladi.' },
    pagination: { emoji: '📄', title: 'Ko\'proq natijalarni ko\'ring',
                  sub: 'Keyingi sahifalar bepul ro\'yxatdan o\'tgach ochiladi.' },
    save:       { emoji: '⭐', title: 'Saqlash uchun ro\'yxatdan o\'ting',
                  sub: 'Dissertatsiyalarni saqlash va eksport qilish uchun hisob kerak.' },
    _default:   { emoji: '📚', title: 'Ro\'yxatdan o\'ting',
                  sub: 'Barcha imkoniyatlar bepul hisob bilan ochiladi.' }
  };

  var modal = null;
  function el(id) { return document.getElementById(id); }

  function openModal(reason) {
    modal = modal || el('loginModal');
    if (!modal) return;
    var r = REASONS[reason] || REASONS._default;
    var t = el('lm-emoji'); if (t) t.textContent = r.emoji;
    t = el('lm-title'); if (t) t.textContent = r.title;
    t = el('lm-sub');   if (t) t.textContent = r.sub;
    modal.classList.add('show');
    document.body.style.overflow = 'hidden';
  }

  function closeModal() {
    modal = modal || el('loginModal');
    if (!modal) return;
    modal.classList.remove('show');
    document.body.style.overflow = '';
  }
  window.closeLoginModal = closeModal;

  // ── Public trigger: CustomEvent 'show-login-modal' ──
  window.addEventListener('show-login-modal', function (e) {
    if (!isGuest()) return;
    openModal((e.detail && e.detail.reason) || '_default');
  });

  document.addEventListener('DOMContentLoaded', function () {
    modal = el('loginModal');
    if (!modal) return;

    var closeBtn = el('lm-close'); if (closeBtn) closeBtn.addEventListener('click', closeModal);
    // backdrop bosilganda yopiladi (kartaning o'zi emas)
    modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && modal.classList.contains('show')) closeModal();
    });

    // Umumiy: [data-login-required] elementlar bosilganda modal (redirect emas)
    document.addEventListener('click', function (e) {
      if (!isGuest()) return;
      var trg = e.target.closest('[data-login-required]');
      if (!trg) return;
      e.preventDefault();
      e.stopPropagation();
      openModal(trg.getAttribute('data-login-required') || '_default');
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
                { detail: { reason: 'pagination', total: d.total } }));
            }
          }).catch(function () {});
        }
      } catch (err) { /* no-op */ }
      return resp;
    });
  };
})();
