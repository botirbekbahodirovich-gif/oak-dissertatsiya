/* Olimlar.uz — chap tezkor panel, mobil pastki qator, "Mening profilim"
   bloki va o'qilmaganlar belgilari.
   Markup: templates/components/quick_rail.html, my_profile_card.html.
   - Panel sahifa tepasida yashirin (inert, Tab bilan o'tilmaydi); ~240px
     pastga aylantirilganda chiqadi, tepaga qaytilganda avval yig'iladi,
     keyin yashirinadi. IntersectionObserver, bo'lmasa passive scroll.
   - Esc yoki tashqarini bosish kengaygan panelni yig'adi.
   - Ctrl+K / Cmd+K — tezkor qidiruv (header'dagi qidiruv oynasi).
   - /api/me/badges — header, panel va profil bloki uchun yagona manba. */
(function () {
  'use strict';

  var SHOW_AFTER = 240;
  var rail = document.querySelector('[data-rail]');
  var bar = document.querySelector('[data-bottombar]');

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { /* private rejim, bloklangan storage — holat saqlanmaydi */ }
    return null;
  }

  // ── Panel: kengaytirish / yig'ish ─────────────────────────────────────────
  var toggles = rail ? rail.querySelectorAll('[data-rail-toggle]') : [];

  function isExpanded() {
    return !!rail && rail.getAttribute('data-expanded') === 'true';
  }

  function setExpanded(open, focusToggle) {
    if (!rail) return;
    rail.setAttribute('data-expanded', open ? 'true' : 'false');
    Array.prototype.forEach.call(toggles, function (t) {
      t.setAttribute('aria-expanded', open ? 'true' : 'false');
      var label = open ? "Panelni yig'ish" : 'Panelni yoyish';
      t.setAttribute('aria-label', label);
      t.setAttribute('title', label);
    });
    if (!open && focusToggle && toggles[0]) toggles[0].focus();
  }

  Array.prototype.forEach.call(toggles, function (t) {
    t.addEventListener('click', function (event) {
      event.stopPropagation();
      setExpanded(!isExpanded());
    });
  });

  document.addEventListener('click', function (event) {
    if (isExpanded() && rail && !rail.contains(event.target)) setExpanded(false);
  });

  // ── Paydo bo'lish ─────────────────────────────────────────────────────────
  var shown = false;

  function setVisible(show) {
    if (show === shown) return;
    shown = show;
    if (!show) setExpanded(false);
    [rail, bar].forEach(function (el) {
      if (!el) return;
      el.setAttribute('data-visible', show ? 'true' : 'false');
      if (show) el.removeAttribute('inert'); else el.setAttribute('inert', '');
    });
    document.body.classList.toggle('ol-bottombar-on', show && !!bar);
  }

  if (rail || bar) {
    if ('IntersectionObserver' in window) {
      var sentinel = document.createElement('div');
      sentinel.setAttribute('aria-hidden', 'true');
      sentinel.style.cssText = 'position:absolute;left:0;top:0;width:1px;height:' + SHOW_AFTER + 'px;pointer-events:none;';
      document.body.appendChild(sentinel);
      new IntersectionObserver(function (entries) {
        // Sentinel (sahifaning birinchi 240px) ko'rinmay qolsa — panel chiqadi.
        setVisible(!entries[0].isIntersecting);
      }).observe(sentinel);
    } else {
      var ticking = false;
      window.addEventListener('scroll', function () {
        if (ticking) return;
        ticking = true;
        window.requestAnimationFrame(function () {
          ticking = false;
          setVisible((window.scrollY || window.pageYOffset || 0) > SHOW_AFTER);
        });
      }, { passive: true });
      setVisible((window.scrollY || window.pageYOffset || 0) > SHOW_AFTER);
    }
  }

  // ── Tezkor qidiruv ────────────────────────────────────────────────────────
  function openQuickSearch() {
    if (window.OlimlarNav && typeof window.OlimlarNav.openSearch === 'function') {
      setExpanded(false);
      window.OlimlarNav.openSearch({ overlay: true });
    } else {
      window.location.href = '/dissertatsiyalar';
    }
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-rail-search]'), function (btn) {
    btn.addEventListener('click', function (event) {
      event.preventDefault();
      openQuickSearch();
    });
  });

  document.addEventListener('keydown', function (event) {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && (event.key === 'k' || event.key === 'K')) {
      event.preventDefault();
      openQuickSearch();
      return;
    }
    if (event.key === 'Escape' && isExpanded()) {
      setExpanded(false, rail.contains(document.activeElement));
    }
  });

  // ── Mobil "Menyu" — mavjud drawer'ni ochadi ───────────────────────────────
  Array.prototype.forEach.call(document.querySelectorAll('[data-bottombar-menu]'), function (btn) {
    btn.addEventListener('click', function () {
      var burger = document.getElementById('ol-burger');
      if (burger) burger.click();
    });
  });

  // ── "Mening profilim" bloki ───────────────────────────────────────────────
  var card = document.getElementById('ol-mycard');

  function setCardCollapsed(collapsed) {
    if (!card) return;
    card.setAttribute('data-state', collapsed ? 'collapsed' : 'expanded');
    store('ol.mycard', collapsed ? 'collapsed' : 'expanded');
  }

  if (card) {
    Array.prototype.forEach.call(card.querySelectorAll('[data-mycard-toggle]'), function (btn) {
      btn.addEventListener('click', function () {
        var collapse = card.getAttribute('data-state') !== 'collapsed';
        setCardCollapsed(collapse);
        // Fokus ko'rinib qolgan holatdagi tugmaga o'tadi.
        var next = card.querySelector(collapse ? '.ol-mycard__compact [data-mycard-toggle]'
                                               : '.ol-mycard__full [data-mycard-toggle]');
        if (next) next.focus();
      });
    });
  }

  // Bosh sahifada panel/pastki qatordagi "Mening profilim" blokni yig'adi/yoyadi;
  // boshqa sahifalarda oddiy havola (profilga olib boradi).
  Array.prototype.forEach.call(document.querySelectorAll('[data-rail-profile]'), function (a) {
    a.addEventListener('click', function (event) {
      if (!card) return;
      event.preventDefault();
      setExpanded(false);
      setCardCollapsed(card.getAttribute('data-state') !== 'collapsed');
      card.scrollIntoView({ block: 'start', behavior: 'smooth' });
      var t = card.querySelector(card.getAttribute('data-state') === 'collapsed'
        ? '.ol-mycard__compact [data-mycard-toggle]' : '.ol-mycard__full [data-mycard-toggle]');
      if (t) t.focus({ preventScroll: true });
    });
  });

  // ── O'qilmaganlar belgilari ──────────────────────────────────────────────
  var badges = document.querySelectorAll('[data-badge]');

  function paint(counts) {
    Array.prototype.forEach.call(badges, function (b) {
      var n = Number(counts[b.getAttribute('data-badge')]) || 0;
      b.textContent = n > 99 ? '99+' : String(n);
      if (n) b.removeAttribute('hidden'); else b.setAttribute('hidden', '');
      var holder = b.closest('[data-badge-label]');
      if (holder) {
        var base = holder.getAttribute('data-badge-label');
        holder.setAttribute('aria-label', n ? base + ': ' + n + " ta o'qilmagan" : base);
      }
    });
  }

  function refresh() {
    if (document.hidden) return;
    fetch('/api/me/badges', { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) paint(d); })
      .catch(function () {});
  }

  if (badges.length && window.OAK_LOGGED_IN !== false) {
    refresh();
    window.setInterval(refresh, 60000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  }
})();
