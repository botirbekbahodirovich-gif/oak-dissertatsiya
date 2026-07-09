/* Acquisition-source survey modal controller.
 *
 * One-time post-signup "Bizni qayerdan bildingiz?" attribution modal. The
 * markup lives in templates/components/acquisition_survey_modal.html and is
 * included from base.html for authenticated non-admin users. Visibility is
 * authoritative on the server: we ask GET /api/acquisition-survey/should-show
 * and only then queue the modal through the shared window.PopupManager so it
 * never stacks with the region / notification popups.
 *
 * Accessibility: focus trap while open, Escape acts as skip (× and
 * "O'tkazib yuborish" too), aria-* set in the partial, focus restored on close.
 */
(function () {
  'use strict';

  var modal = document.getElementById('acqSurvey');
  if (!modal) return;

  // Never interrupt these flows (should-show also excludes admins server-side).
  var SKIP_PATHS = ['/admin', '/login', '/register', '/offline', '/cabinet/onboarding'];
  var path = window.location.pathname;
  if (SKIP_PATHS.some(function (p) { return path.indexOf(p) === 0; })) return;

  var selected = null;             // chosen source string
  var submitting = false;
  var lastFocus = null;            // element focused before the modal opened
  var tiles = Array.prototype.slice.call(modal.querySelectorAll('.acq-tile'));
  var otherWrap = document.getElementById('acqOtherWrap');
  var otherInput = document.getElementById('acqOtherInput');
  var submitBtn = document.getElementById('acqSubmit');
  var skipBtn = document.getElementById('acqSkip');
  var closeBtn = document.getElementById('acqClose');
  var errBox = document.getElementById('acqErr');

  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body || {})
    });
  }

  // ── enable/disable the submit button based on current selection ──
  function refreshSubmit() {
    var ok = !!selected;
    if (selected === 'other') {
      ok = otherInput.value.trim().length > 0;
    }
    submitBtn.disabled = !ok;
  }

  function selectTile(tile) {
    selected = tile.getAttribute('data-source');
    tiles.forEach(function (t) {
      var on = t === tile;
      t.classList.toggle('sel', on);
      t.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    var isOther = selected === 'other';
    otherWrap.classList.toggle('show', isOther);
    submitBtn.classList.add('show');
    if (isOther) { otherInput.focus(); }
    refreshSubmit();
  }

  tiles.forEach(function (tile) {
    tile.setAttribute('aria-pressed', 'false');
    tile.addEventListener('click', function () { selectTile(tile); });
  });
  otherInput.addEventListener('input', refreshSubmit);

  // ── open / close with fade ──
  function open() {
    lastFocus = document.activeElement;
    modal.classList.add('show');
    document.body.style.overflow = 'hidden';
    // next frame → trigger the opacity/transform transition
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { modal.classList.add('visible'); });
    });
    (closeBtn || modal).focus();
  }

  function close() {
    modal.classList.remove('visible');
    var finish = function () {
      modal.classList.remove('show');
      document.body.style.overflow = '';
      modal.removeEventListener('transitionend', finish);
      if (lastFocus && typeof lastFocus.focus === 'function') {
        try { lastFocus.focus(); } catch (e) { /* ignore */ }
      }
      // Release the popup queue so region / notifications can follow.
      if (window.PopupManager) window.PopupManager.done();
    };
    modal.addEventListener('transitionend', finish);
    // Fallback if transitionend never fires.
    setTimeout(finish, 350);
  }

  function skip() {
    if (submitting) return;
    // Fire-and-forget; close immediately for a snappy feel.
    postJSON('/api/acquisition-survey/skip').catch(function () {});
    close();
  }

  function submit() {
    if (submitting || submitBtn.disabled) return;
    submitting = true;
    errBox.textContent = '';
    submitBtn.disabled = true;
    var original = submitBtn.textContent;
    submitBtn.textContent = 'Yuborilmoqda…';
    var payload = { source: selected };
    if (selected === 'other') payload.source_other = otherInput.value.trim().slice(0, 200);

    postJSON('/api/acquisition-survey/submit', payload)
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (res.ok && (res.d.status === 'ok' || res.d.status === 'already_answered')) {
          close();
        } else {
          throw new Error(res.d && res.d.error ? res.d.error : 'submit_failed');
        }
      })
      .catch(function () {
        errBox.textContent = 'Xatolik yuz berdi. Qaytadan urinib ko\'ring.';
        submitting = false;
        submitBtn.textContent = original;
        refreshSubmit();
      });
  }

  submitBtn.addEventListener('click', submit);
  skipBtn.addEventListener('click', skip);
  closeBtn.addEventListener('click', skip);
  // Clicking the dim backdrop (outside the card) acts as skip.
  modal.addEventListener('click', function (e) { if (e.target === modal) skip(); });

  // ── focus trap + Escape (= skip) while open ──
  document.addEventListener('keydown', function (e) {
    if (!modal.classList.contains('show')) return;
    if (e.key === 'Escape') { e.preventDefault(); skip(); return; }
    if (e.key !== 'Tab') return;
    var focusable = modal.querySelectorAll(
      'button:not([disabled]), input, [tabindex]:not([tabindex="-1"])');
    focusable = Array.prototype.filter.call(focusable, function (el) {
      return el.offsetParent !== null; // visible only
    });
    if (!focusable.length) return;
    var first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  // ── decide whether to show, then queue via the popup manager ──
  function maybeShow() {
    fetch('/api/acquisition-survey/should-show', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.should_show) return;
        // priority 2 (onboarding tier). `force = true` bypasses the minimal-UI
        // master switch — this survey is an explicit one-time ask.
        if (window.PopupManager) window.PopupManager.request(2, open, true);
        else open();
      })
      .catch(function () {});
  }

  // Give the page a moment to settle after load, then ask.
  setTimeout(maybeShow, 1500);
})();
