/* Metadata sinxronizatsiya dashboard — sync detail sahifasi (Phase 2C.3B).
 *
 * Diff jadval SERVER tomonidan allaqachon render qilingan (Jinja) — bu skript
 * FAQAT "Qo'llash" amalini bajaradi: tanlangan maydon nomlarini yig'adi va
 * MAVJUD apply endpointiga (`services.article_sync.apply_article_metadata_changes`,
 * Phase 2C.3A) yuboradi — yangi apply logika YO'Q (§11).
 */
(function () {
  'use strict';

  var form = document.getElementById('msd-apply-form');
  var applyBtn = document.getElementById('msd-apply-btn');
  if (!form || !applyBtn) { return; }

  var statusEl = document.getElementById('msd-status');
  var confirmRetraction = document.getElementById('msd-confirm-retraction');
  var confirmDoi = document.getElementById('msd-confirm-doi');
  var applyEndpoint = form.getAttribute('data-apply-endpoint');
  var syncId = parseInt(form.getAttribute('data-sync-id'), 10);

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function setStatus(text) {
    if (!statusEl) { return; }
    statusEl.textContent = text;
    statusEl.hidden = !text;
  }

  applyBtn.addEventListener('click', function () {
    var fields = Array.prototype.slice
      .call(form.querySelectorAll('input[name="fields"]'))
      .filter(function (cb) { return cb.checked; })
      .map(function (cb) { return cb.value; });

    if (!fields.length) {
      setStatus('Hech qanday maydon tanlanmadi.');
      return;
    }
    if (confirmRetraction && !confirmRetraction.checked) {
      setStatus('Retraction o‘zgarishini tasdiqlang.');
      return;
    }
    if (confirmDoi && !confirmDoi.checked) {
      setStatus('DOI nomuvofiqligini tasdiqlang.');
      return;
    }

    applyBtn.disabled = true;
    setStatus("Qo'llanmoqda…");

    fetch(applyEndpoint, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sync_id: syncId, fields: fields,
        confirm_retraction: !!(confirmRetraction && confirmRetraction.checked),
        confirm_doi_mismatch: !!(confirmDoi && confirmDoi.checked)
      })
    }).then(function (resp) {
      return resp.json().then(function (body) { return { ok: resp.ok, body: body }; });
    }).then(function (res) {
      if (!res.ok) {
        applyBtn.disabled = false;
        setStatus(res.body.message || 'Xato yuz berdi.');
        return;
      }
      setStatus("O'zgarishlar qo'llandi. Sahifa yangilanmoqda…");
      window.location.reload();
    }).catch(function () {
      applyBtn.disabled = false;
      setStatus("Ulanish xatosi. Qayta urinib ko'ring.");
    });
  });
})();
