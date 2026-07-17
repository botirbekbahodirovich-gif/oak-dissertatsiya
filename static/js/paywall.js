/* Paywall modal — barcha pullik funksiyalar uchun yagona komponent.
 *
 * window.showPaywall({
 *   feature_name:     'AI mavzu tahlili',
 *   one_time_product: 'topic_analysis_1',   // /api/pay/create product_code
 *   one_time_price:   5000,                 // so'mda, faqat KO'RSATISH uchun
 *   benefits:         ['...', '...']        // ixtiyoriy
 * })
 *
 * Ikki CTA: bir martalik xarid (→ ATMOS checkout) va Premium (→ /premium).
 * Hech qachon allaqachon ochiq kontentni bloklamaydi — faqat chaqirilganda
 * ko'rinadi. Narxlar serverda (blueprints/payments.PRICES) tekshiriladi.
 */
(function () {
  'use strict';

  var overlay = null;

  function fmt(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function close() {
    if (overlay) { overlay.remove(); overlay = null; }
    document.removeEventListener('keydown', onKey);
  }

  function onKey(e) { if (e.key === 'Escape') close(); }

  function buyOneTime(btn, product) {
    var old = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Yuklanmoqda…';
    fetch('/api/pay/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_code: product })
    }).then(function (r) {
      if (r.redirected && r.url.indexOf('/login') !== -1) {
        window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
        return null;
      }
      return r.json().then(function (d) { return { ok: r.ok, data: d }; });
    }).then(function (res) {
      if (!res) return;
      if (res.ok && res.data.redirect_url) {
        window.location.href = res.data.redirect_url;
        return;
      }
      btn.disabled = false;
      btn.textContent = old;
      var errEl = overlay && overlay.querySelector('.pw-err');
      if (errEl) {
        errEl.textContent = (res.data && res.data.message) ||
          "To'lovni boshlashda xatolik. Qayta urinib ko'ring.";
        errEl.style.display = 'block';
      }
    }).catch(function () {
      btn.disabled = false;
      btn.textContent = old;
    });
  }

  function ensureStyles() {
    if (document.getElementById('pw-styles')) return;
    var css = '' +
      '.pw-overlay{position:fixed;inset:0;background:rgba(2,6,23,0.72);z-index:10050;' +
        'display:flex;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(3px);}' +
      '.pw-modal{background:#1e293b;border:1px solid #334155;border-radius:18px;max-width:440px;' +
        'width:100%;padding:28px;position:relative;box-shadow:0 20px 60px rgba(0,0,0,0.5);}' +
      '.pw-close{position:absolute;top:12px;right:14px;background:none;border:none;color:#64748b;' +
        'font-size:1.4rem;cursor:pointer;line-height:1;}' +
      '.pw-close:hover{color:#e2e8f0;}' +
      '.pw-icon{font-size:2rem;margin-bottom:8px;}' +
      '.pw-title{font-size:1.15rem;font-weight:800;color:#f1f5f9;margin:0 0 6px;}' +
      '.pw-sub{color:#94a3b8;font-size:0.9rem;margin:0 0 14px;}' +
      '.pw-benefits{margin:0 0 18px;padding:0;list-style:none;}' +
      '.pw-benefits li{color:#cbd5e1;font-size:0.88rem;padding:4px 0 4px 24px;position:relative;}' +
      '.pw-benefits li:before{content:"✓";position:absolute;left:2px;color:#4ade80;font-weight:800;}' +
      '.pw-btn{display:block;width:100%;border:none;border-radius:12px;padding:12px 18px;' +
        'font-weight:800;font-size:0.95rem;cursor:pointer;text-align:center;text-decoration:none;' +
        'margin-bottom:10px;transition:opacity 0.15s;}' +
      '.pw-btn:hover{opacity:0.92;}' +
      '.pw-btn:disabled{opacity:0.55;cursor:not-allowed;}' +
      '.pw-btn.once{background:#334155;color:#e2e8f0;}' +
      '.pw-btn.prem{background:linear-gradient(135deg,#3b82f6,#7c5cfc);color:#fff;}' +
      '.pw-err{display:none;margin-top:8px;background:rgba(239,68,68,0.12);color:#fca5a5;' +
        'border-radius:10px;padding:10px 14px;font-size:0.85rem;}' +
      '.pw-legal{margin-top:10px;color:#64748b;font-size:0.75rem;text-align:center;}' +
      '.pw-legal a{color:#60a5fa;text-decoration:none;}' +
      '[data-bs-theme="light"] .pw-modal{background:#fff;border-color:#e2e8f0;}' +
      '[data-bs-theme="light"] .pw-title{color:#0f172a;}' +
      '[data-bs-theme="light"] .pw-benefits li{color:#334155;}' +
      '[data-bs-theme="light"] .pw-btn.once{background:#eef2f7;color:#0f172a;}';
    var el = document.createElement('style');
    el.id = 'pw-styles';
    el.textContent = css;
    document.head.appendChild(el);
  }

  window.showPaywall = function (opts) {
    opts = opts || {};
    ensureStyles();
    close();
    var benefits = (opts.benefits || []).map(function (b) {
      return '<li>' + esc(b) + '</li>';
    }).join('');
    overlay = document.createElement('div');
    overlay.className = 'pw-overlay';
    overlay.innerHTML =
      '<div class="pw-modal" role="dialog" aria-modal="true" aria-label="Premium">' +
        '<button type="button" class="pw-close" aria-label="Yopish">×</button>' +
        '<div class="pw-icon">🔓</div>' +
        '<h3 class="pw-title">' + esc(opts.feature_name || 'Premium imkoniyat') + '</h3>' +
        '<p class="pw-sub">Bepul limit tugadi. Davom etish uchun tanlang:</p>' +
        (benefits ? '<ul class="pw-benefits">' + benefits + '</ul>' : '') +
        (opts.one_time_product
          ? '<button type="button" class="pw-btn once" data-product="' +
              esc(opts.one_time_product) + '">Bir martalik — ' +
              fmt(opts.one_time_price || '') + ' so\'m</button>'
          : '') +
        '<a class="pw-btn prem" href="/premium">⭐ Premium — 29 000 so\'m/oy</a>' +
        '<div class="pw-err"></div>' +
        '<div class="pw-legal">To\'lov ATMOS orqali (UzCard/Humo) · ' +
          '<a href="/oferta">Oferta</a> · <a href="/tolovni-qaytarish">Qaytarish</a></div>' +
      '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) close();
    });
    overlay.querySelector('.pw-close').addEventListener('click', close);
    var onceBtn = overlay.querySelector('.pw-btn.once');
    if (onceBtn) {
      onceBtn.addEventListener('click', function () {
        buyOneTime(onceBtn, onceBtn.dataset.product);
      });
    }
    document.addEventListener('keydown', onKey);
  };

  window.hidePaywall = close;
})();
