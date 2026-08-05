/* Olimlar.uz — navigatsiya xatti-harakati.
   Desktop dropdown (click), mobil drawer (accordion), qidiruv overlay.
   Barcha markup site_header.html da; bu yerda faqat interaktivlik. */
(function () {
  'use strict';

  var FOCUSABLE = 'a[href], button:not([disabled]), input, [tabindex]:not([tabindex="-1"])';

  // ── Desktop dropdownlar ────────────────────────────────────────────────────
  var triggers = Array.prototype.slice.call(
    document.querySelectorAll('[data-nav-trigger]')
  );

  function menuOf(trigger) {
    return document.getElementById(trigger.getAttribute('aria-controls'));
  }

  function closeMenu(trigger) {
    var menu = menuOf(trigger);
    trigger.setAttribute('aria-expanded', 'false');
    if (menu) menu.setAttribute('data-open', 'false');
  }

  function closeAllMenus(except) {
    triggers.forEach(function (t) { if (t !== except) closeMenu(t); });
  }

  function openMenu(trigger) {
    // Bir vaqtda faqat bitta dropdown ochiq bo'ladi.
    closeAllMenus(trigger);
    var menu = menuOf(trigger);
    trigger.setAttribute('aria-expanded', 'true');
    if (!menu) return;
    menu.setAttribute('data-open', 'true');
    // Ekrandan chiqib ketmasin.
    menu.classList.remove('ol-menu--end');
    var rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) menu.classList.add('ol-menu--end');
  }

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (trigger.getAttribute('aria-expanded') === 'true') closeMenu(trigger);
      else openMenu(trigger);
    });

    trigger.addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowDown') return;
      event.preventDefault();
      openMenu(trigger);
      var menu = menuOf(trigger);
      var first = menu && menu.querySelector(FOCUSABLE);
      if (first) first.focus();
    });

    // Hover — yordamchi, lekin yagona mexanizm emas.
    var item = trigger.closest('[data-nav-item]');
    if (!item) return;
    item.addEventListener('mouseenter', function () {
      if (window.matchMedia('(hover: hover) and (min-width: 1200px)').matches) openMenu(trigger);
    });
    item.addEventListener('mouseleave', function () {
      if (window.matchMedia('(hover: hover) and (min-width: 1200px)').matches) closeMenu(trigger);
    });
  });

  document.addEventListener('click', function (event) {
    var openTrigger = triggers.filter(function (t) {
      return t.getAttribute('aria-expanded') === 'true';
    });
    openTrigger.forEach(function (t) {
      var item = t.closest('[data-nav-item]');
      if (item && !item.contains(event.target)) closeMenu(t);
    });
  });

  // ── Mobil drawer ───────────────────────────────────────────────────────────
  var drawer = document.getElementById('ol-drawer');
  var burger = document.getElementById('ol-burger');
  var drawerClose = document.getElementById('ol-drawer-close');
  var lastFocused = null;

  function drawerIsOpen() {
    return !!drawer && drawer.getAttribute('data-open') === 'true';
  }

  function openDrawer() {
    if (!drawer) return;
    lastFocused = document.activeElement;
    drawer.setAttribute('data-open', 'true');
    document.body.classList.add('ol-scroll-locked');
    if (burger) burger.setAttribute('aria-expanded', 'true');
    if (drawerClose) drawerClose.focus();
  }

  function closeDrawer() {
    if (!drawer) return;
    drawer.setAttribute('data-open', 'false');
    document.body.classList.remove('ol-scroll-locked');
    if (burger) burger.setAttribute('aria-expanded', 'false');
    if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
  }

  if (burger) burger.addEventListener('click', openDrawer);
  if (drawerClose) drawerClose.addEventListener('click', closeDrawer);
  if (drawer) {
    var backdrop = drawer.querySelector('[data-drawer-backdrop]');
    if (backdrop) backdrop.addEventListener('click', closeDrawer);
    // Route tanlanganda drawer avtomatik yopiladi.
    drawer.addEventListener('click', function (event) {
      if (event.target.closest('a[href]')) closeDrawer();
    });
  }

  // Accordion — drawer ichidagi bo'limlar.
  Array.prototype.forEach.call(
    document.querySelectorAll('[data-acc-trigger]'),
    function (trigger) {
      trigger.addEventListener('click', function () {
        var panel = document.getElementById(trigger.getAttribute('aria-controls'));
        var open = trigger.getAttribute('aria-expanded') === 'true';
        trigger.setAttribute('aria-expanded', open ? 'false' : 'true');
        if (panel) panel.setAttribute('data-open', open ? 'false' : 'true');
      });
    }
  );

  // ── Qidiruv overlay ────────────────────────────────────────────────────────
  var searchToggles = document.querySelectorAll('[data-search-toggle]');
  var searchPanel = document.getElementById('ol-search');
  var searchInput = document.getElementById('ol-search-input');
  var searchClose = document.getElementById('ol-search-close');
  var searchLastFocused = null;

  function searchIsOpen() {
    return !!searchPanel && searchPanel.getAttribute('data-open') === 'true';
  }

  function openSearch() {
    // Bosh sahifadagi hero qidiruvi mavjud bo'lsa — o'shanga fokus (mavjud xulq).
    var hero = document.getElementById('hx-search-input');
    if (hero) {
      hero.scrollIntoView({ block: 'center' });
      hero.focus({ preventScroll: true });
      return;
    }
    if (!searchPanel) return;
    closeDrawer();
    closeAllMenus(null);
    searchLastFocused = document.activeElement;
    searchPanel.setAttribute('data-open', 'true');
    document.body.classList.add('ol-scroll-locked');
    if (searchInput) searchInput.focus();
  }

  function closeSearch() {
    if (!searchPanel) return;
    searchPanel.setAttribute('data-open', 'false');
    if (!drawerIsOpen()) document.body.classList.remove('ol-scroll-locked');
    if (searchLastFocused && typeof searchLastFocused.focus === 'function') {
      searchLastFocused.focus();
    }
  }

  Array.prototype.forEach.call(searchToggles, function (btn) {
    btn.addEventListener('click', function (event) {
      event.preventDefault();
      openSearch();
    });
  });
  if (searchClose) searchClose.addEventListener('click', closeSearch);
  if (searchPanel) {
    searchPanel.addEventListener('click', function (event) {
      if (event.target === searchPanel) closeSearch();
    });
  }

  // ── Umumiy klaviatura ──────────────────────────────────────────────────────
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeAllMenus(null);
      if (searchIsOpen()) closeSearch();
      else if (drawerIsOpen()) closeDrawer();
      return;
    }
    // Fokus drawer ichida qoladi.
    if (event.key === 'Tab' && drawerIsOpen()) {
      var panel = drawer.querySelector('.ol-drawer__panel');
      if (!panel) return;
      var items = Array.prototype.filter.call(
        panel.querySelectorAll(FOCUSABLE),
        function (el) { return el.offsetParent !== null; }
      );
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
})();
