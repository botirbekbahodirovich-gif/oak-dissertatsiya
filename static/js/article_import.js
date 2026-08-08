/* Ommaviy DOI/CSV import — review-navbat sahifasi (Phase 2C.2).
 *
 * Bitta HTTP so'rov 500 DOI tugaguncha OSILIB QOLMASLIGI kerak (§20), shuning
 * uchun server bitta chunk (services/article_import/config.py:CHUNK_SIZE)
 * ishlaydi va qaytadi. Bu skript shu endpointni QAYTA-QAYTA chaqiradi —
 * bir vaqtning o'zida ham progress ko'rsatkichi, ham processing drayveri
 * (§44). Sahifa yopilsa yurish to'xtaydi, lekin element holati DB'da
 * saqlangani uchun (har chunk commit qiladi) keyingi ochilishda "Davom
 * ettirish" bilan xuddi shu joydan davom etadi — hech narsa yo'qolmaydi.
 */
(function () {
  'use strict';

  var page = document.querySelector('[data-ai-batch-id]');
  if (!page) { return; }

  var batchId = page.getAttribute('data-ai-batch-id');
  var button = document.getElementById('ai-process-btn');
  var progressPanel = document.getElementById('ai-progress');
  var progressText = document.getElementById('ai-progress-text');
  var progressFill = document.getElementById('ai-progress-fill');
  var summaryEls = document.getElementById('ai-summary');

  var IN_FLIGHT_STATUSES = ['queued', 'processing'];
  var POLL_DELAY_MS = 2500;
  var running = false;
  var stopped = false;

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function updateProgress(batch) {
    if (!batch) { return; }
    var remaining = batch.pending_count + batch.processing_count;
    if (progressText) {
      progressText.textContent = remaining > 0
        ? ('Ishlanmoqda: ' + remaining + ' ta qoldi.')
        : 'Ishlash yakunlandi.';
    }
    if (progressFill && batch.total_items) {
      var done = batch.total_items - remaining;
      progressFill.style.width = Math.max(0, Math.min(100, (done / batch.total_items) * 100)) + '%';
    }
  }

  function stepOnce() {
    return fetch('/admin/articles/imports/' + batchId + '/process', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
    }).then(function (resp) {
      if (!resp.ok) { throw new Error('http_' + resp.status); }
      return resp.json();
    });
  }

  function driveLoop() {
    if (stopped) { return; }
    stepOnce().then(function (data) {
      updateProgress(data.batch);
      if (IN_FLIGHT_STATUSES.indexOf(data.batch.status) !== -1 && !data.batch.cancel_requested) {
        window.setTimeout(driveLoop, POLL_DELAY_MS);
      } else {
        running = false;
        if (button) { button.disabled = false; }
        window.location.reload();
      }
    }).catch(function () {
      running = false;
      if (button) { button.disabled = false; }
      if (progressText) { progressText.textContent = 'Ulanish xatosi. "Davom ettirish"ni qayta bosing.'; }
    });
  }

  function start() {
    if (running) { return; }
    running = true;
    if (button) { button.disabled = true; }
    if (progressPanel) { progressPanel.hidden = false; }
    driveLoop();
  }

  if (button) {
    button.addEventListener('click', start);
  }

  window.addEventListener('beforeunload', function () { stopped = true; });

  var initialStatus = page.getAttribute('data-ai-status');
  var cancelRequested = page.getAttribute('data-ai-cancel-requested') === 'true';
  if (IN_FLIGHT_STATUSES.indexOf(initialStatus) !== -1 && !cancelRequested) {
    start();
  }
})();
