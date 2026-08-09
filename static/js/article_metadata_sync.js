/* Mavjud maqola metadata sinxronizatsiyasi — admin maqola formasi
 * (Phase 2C.3A).
 *
 * `maqolalar_metadata.js` bilan bir xil uslub: IIFE, vanilla, framework yo'q.
 * Tashqi matn HECH QACHON HTML sifatida qo'yilmaydi — faqat `textContent`.
 *
 * Ikki qat'iy qoida:
 *   1. "Tekshirish" hech narsani article'ga YOZMAYDI — faqat diff ko'rsatadi.
 *   2. "Qo'llash" faqat CHECKBOX orqali admin TANLAGAN maydon nomlarini
 *      yuboradi; server YANGI QIYMATNI o'zi saqlangan sync yozuvidan oladi
 *      (client hech qanday qiymat yubormaydi, faqat maydon nomlari + sync_id).
 */
(function () {
  'use strict';

  var root = document.querySelector('[data-mq-sync]');
  if (!root) { return; }

  var checkBtn = root.querySelector('[data-mq-sync-check]');
  var statusEl = root.querySelector('[data-mq-sync-status]');
  var resultEl = root.querySelector('[data-mq-sync-result]');
  var summaryEl = root.querySelector('[data-mq-sync-summary]');
  var warningsEl = root.querySelector('[data-mq-sync-warnings]');
  var rowsEl = root.querySelector('[data-mq-sync-rows]');
  var applyBtn = root.querySelector('[data-mq-sync-apply]');
  var confirmRetraction = root.querySelector('[data-mq-sync-confirm-retraction]');
  var confirmRetractionInput = root.querySelector('[data-mq-sync-confirm-retraction-input]');
  var confirmDoi = root.querySelector('[data-mq-sync-confirm-doi]');
  var confirmDoiInput = root.querySelector('[data-mq-sync-confirm-doi-input]');

  var checkEndpoint = root.getAttribute('data-check-endpoint');
  var applyEndpoint = root.getAttribute('data-apply-endpoint');
  var currentSyncId = null;

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function setStatus(text) {
    statusEl.textContent = text;
    statusEl.hidden = !text;
  }

  function clearNode(node) {
    while (node.firstChild) { node.removeChild(node.firstChild); }
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === '') { return '—'; }
    if (Array.isArray(value)) {
      if (value.length && typeof value[0] === 'object') {
        return value.map(function (v) { return v.name || JSON.stringify(v); }).join(', ');
      }
      return value.join(', ');
    }
    if (typeof value === 'boolean') { return value ? 'Ha' : "Yo'q"; }
    return String(value);
  }

  function renderWarnings(warnings) {
    clearNode(warningsEl);
    (warnings || []).forEach(function (w) {
      var div = document.createElement('div');
      div.className = 'mq-sync__warning';
      div.textContent = w.message || w.code || '';
      warningsEl.appendChild(div);
    });
  }

  function renderRows(diffs) {
    clearNode(rowsEl);
    diffs.forEach(function (diff) {
      var tr = document.createElement('tr');
      tr.className = 'mq-sync__row--' + diff.severity;

      var tdCheck = document.createElement('td');
      if (diff.applicable) {
        var checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.setAttribute('data-mq-sync-field', diff.field);
        checkbox.setAttribute('aria-label', 'Tanlash: ' + diff.label);
        checkbox.checked = !!diff.default_selected;
        tdCheck.appendChild(checkbox);
      }
      tr.appendChild(tdCheck);

      var tdField = document.createElement('td');
      tdField.textContent = diff.label;
      tr.appendChild(tdField);

      var tdOld = document.createElement('td');
      tdOld.textContent = formatValue(diff.old_value);
      tr.appendChild(tdOld);

      var tdNew = document.createElement('td');
      tdNew.textContent = formatValue(diff.new_value);
      if (diff.note) {
        var note = document.createElement('span');
        note.className = 'mq-sync__note';
        note.textContent = diff.note;
        tdNew.appendChild(note);
      }
      tr.appendChild(tdNew);

      var tdSource = document.createElement('td');
      tdSource.textContent = diff.source || '—';
      tr.appendChild(tdSource);

      var tdSeverity = document.createElement('td');
      tdSeverity.textContent = diff.severity_label;
      tr.appendChild(tdSeverity);

      rowsEl.appendChild(tr);
    });
  }

  function renderResult(body) {
    currentSyncId = body.sync_id;
    renderWarnings(body.warnings);
    renderRows(body.diffs);

    if (body.change_count === 0) {
      summaryEl.textContent = 'Metadata yangilangan. Muhim o‘zgarish topilmadi.';
      applyBtn.disabled = true;
    } else {
      summaryEl.textContent = body.change_count + ' ta o‘zgarish topildi: ' +
        body.safe_count + ' xavfsiz, ' + body.review_count + ' ko‘rib chiqish, ' +
        body.critical_count + ' MUHIM.';
      applyBtn.disabled = false;
    }

    var hasRetraction = body.diffs.some(function (d) {
      return d.field === 'is_retracted' && d.severity === 'critical';
    });
    confirmRetraction.hidden = !hasRetraction;
    confirmRetractionInput.checked = false;

    confirmDoi.hidden = !body.doi_mismatch;
    confirmDoiInput.checked = false;
    if (body.doi_mismatch) { applyBtn.disabled = true; }

    resultEl.hidden = false;
  }

  function runCheck() {
    checkBtn.disabled = true;
    setStatus('Metadata tekshirilmoqda…');
    resultEl.hidden = true;

    fetch(checkEndpoint, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken(), 'Content-Type': 'application/json' },
      body: '{}'
    }).then(function (resp) {
      return resp.json().then(function (body) { return { ok: resp.ok, body: body }; });
    }).then(function (res) {
      checkBtn.disabled = false;
      if (!res.ok) {
        setStatus(res.body.message || 'Xato yuz berdi.');
        return;
      }
      setStatus('');
      renderResult(res.body);
    }).catch(function () {
      checkBtn.disabled = false;
      setStatus("Ulanish xatosi. Qayta urinib ko'ring.");
    });
  }

  function runApply() {
    if (!currentSyncId) { return; }
    var fields = Array.prototype.slice
      .call(rowsEl.querySelectorAll('[data-mq-sync-field]'))
      .filter(function (cb) { return cb.checked; })
      .map(function (cb) { return cb.getAttribute('data-mq-sync-field'); });
    if (!fields.length) {
      setStatus('Hech qanday maydon tanlanmadi.');
      return;
    }
    if (!confirmRetraction.hidden && !confirmRetractionInput.checked) {
      setStatus('Retraction o‘zgarishini tasdiqlang.');
      return;
    }
    if (!confirmDoi.hidden && !confirmDoiInput.checked) {
      setStatus('DOI nomuvofiqligini tasdiqlang.');
      return;
    }

    applyBtn.disabled = true;
    setStatus("Qo'llanmoqda…");

    fetch(applyEndpoint, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sync_id: currentSyncId, fields: fields,
        confirm_retraction: confirmRetractionInput.checked,
        confirm_doi_mismatch: confirmDoiInput.checked
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
  }

  checkBtn.addEventListener('click', runCheck);
  applyBtn.addEventListener('click', runApply);
})();
