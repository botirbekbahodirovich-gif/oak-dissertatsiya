/* DOI metadata autofill — admin maqola formasi (Phase 2C.1).
 *
 * Mavjud JS uslubi saqlanadi: IIFE, vanilla, framework yo'q. Fayl FAQAT admin
 * formasiga ulanadi — katalog sahifalariga yuborilmaydi.
 *
 * Uchta qat'iy qoida bu faylni belgilaydi:
 *
 *   1. Hech qanday maydon O'ZIDAN o'zgarmaydi. "Bo'sh maydonlarni to'ldirish"
 *      faqat BO'SH maydonlarni to'ldiradi; to'lgan maydon yonma-yon
 *      solishtiriladi va alohida "Almashtirish" bilan yangilanadi (§11).
 *   2. Bu tugma hech nimani saqlamaydi. Ma'lumot admin "Saqlash" bosgunicha
 *      faqat formada turadi (§13).
 *   3. Tashqi matn HECH QACHON HTML sifatida qo'yilmaydi — DOM tugunlari
 *      qo'lda quriladi va matn faqat `textContent` orqali qo'yiladi (§31).
 *      HTML satrini elementga tiqadigan API bu faylda ishlatilmaydi;
 *      buni test qulflab turadi.
 */
(function () {
  'use strict';

  var root = document.querySelector('[data-mq-meta]');
  var fetchButton = document.querySelector('[data-mq-meta-fetch]');
  var doiInput = document.getElementById('f-doi');
  if (!root || !fetchButton || !doiInput) return;

  var REQUEST_TIMEOUT_MS = 30000;

  var el = {
    status: root.querySelector('[data-mq-meta-status]'),
    providers: root.querySelector('[data-mq-meta-providers]'),
    alerts: root.querySelector('[data-mq-meta-alerts]'),
    fill: root.querySelector('[data-mq-meta-fill]'),
    toggle: root.querySelector('[data-mq-meta-toggle]'),
    details: root.querySelector('[data-mq-meta-details]'),
    rows: root.querySelector('[data-mq-meta-rows]'),
    extras: root.querySelector('[data-mq-meta-extras]')
  };

  /* Nomzod maydon -> formadagi element. `bulk: false` — guruh tugmasi bu
     maydonga TEGMAYDI. Litsenziya ataylab shunday: u huquqiy qaror va har
     doim alohida bosish talab qiladi (§28). */
  var FIELDS = [
    { key: 'title', label: 'Maqola nomi', id: 'f-title', bulk: true },
    { key: 'abstract', label: 'Annotatsiya', id: 'f-abstract', bulk: true },
    { key: 'keywords', label: 'Kalit so‘zlar', id: 'f-keywords', bulk: true },
    { key: 'language', label: 'Til', id: 'f-language', type: 'select', bulk: true },
    { key: 'journal_name', label: 'Jurnal nomi', id: 'f-journal', bulk: true },
    { key: 'publication_year', label: 'Nashr yili', id: 'f-year', bulk: true },
    { key: 'publication_date', label: 'Nashr sanasi', id: 'f-date', bulk: true },
    { key: 'volume', label: 'Jild', id: 'f-volume', bulk: true },
    { key: 'issue', label: 'Son', id: 'f-issue', bulk: true },
    { key: 'pages', label: 'Betlar', id: 'f-pages', bulk: true },
    { key: 'external_url', label: 'Tashqi havola', id: 'f-external', bulk: true },
    { key: 'license_type', label: 'Litsenziya', id: 'f-license', type: 'select', bulk: false },
    { key: 'license_url', label: 'Litsenziya havolasi', id: 'f-license-url', bulk: false },
    { key: 'is_retracted', label: 'Chaqirib olingan', id: 'f-retracted', type: 'checkbox', bulk: true }
  ];

  /* Yashirin identifikatorlar — jadvalda ko'rsatilmaydi, chunki admin ularni
     baholay olmaydi. Ular guruh tugmasi bilan birga qo'yiladi. */
  var HIDDEN_KEYS = ['openalex_id', 'crossref_member_id', 'primary_issn',
                     'citation_count', 'citation_count_source',
                     'citation_count_checked_at'];

  var state = { data: null, doi: '', stale: false, controller: null, busy: false };

  // ── Kichik yordamchilar ───────────────────────────────────────────────────

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function field(entry) {
    return document.getElementById(entry.id);
  }

  function readValue(entry) {
    var node = field(entry);
    if (!node) return '';
    if (entry.type === 'checkbox') return node.checked ? '1' : '';
    return (node.value || '').trim();
  }

  function isEmpty(entry) {
    if (entry.type === 'checkbox') return !readValue(entry);
    return readValue(entry) === '';
  }

  /* Solishtirish uchun normalizatsiya: registr va ortiqcha bo'shliqlar farq
     hisoblanmaydi, aks holda "Journal Of A" va "Journal of A" konflikt
     bo'lib ko'rinardi. */
  function compareKey(value) {
    return String(value === null || value === undefined ? '' : value)
      .toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function candidateValue(entry, metadata) {
    var value = metadata[entry.key];
    if (entry.key === 'keywords') {
      var words = (metadata.keywords || []).map(function (k) { return k.name; });
      return words.length ? words.join(', ') : '';
    }
    if (entry.key === 'license_type' || entry.key === 'license_url') {
      var picked = pickLicense(metadata);
      if (!picked) return '';
      return entry.key === 'license_type' ? (picked.license_type || '') : (picked.url || '');
    }
    if (entry.type === 'checkbox') return value ? '1' : '';
    if (value === null || value === undefined) return '';
    return String(value);
  }

  /* Litsenziya nomzodi: tanib olingan tur bo'lgan BIRINCHISI. Bir nechta
     bir-biriga zid tur bo'lsa server `conflicts` ichida aytadi va admin
     qaror qiladi — bu yerda "eng yaxshisi" tanlanmaydi. */
  function pickLicense(metadata) {
    var candidates = metadata.license_candidates || [];
    for (var i = 0; i < candidates.length; i += 1) {
      if (candidates[i].license_type) return candidates[i];
    }
    return null;
  }

  /* <select> uchun: qiymat mavjud variantlardan bo'lsagina qo'llanadi.
     Mos kelmasa taxmin qilinmaydi (masalan noma'lum til kodi "other" ga
     aylantirilmaydi) — admin o'zi tanlaydi. */
  function selectHasOption(node, value) {
    if (!node || !value) return false;
    for (var i = 0; i < node.options.length; i += 1) {
      if (node.options[i].value === value) return true;
    }
    return false;
  }

  function applyField(entry, value) {
    var node = field(entry);
    if (!node || value === '') return false;
    if (entry.type === 'checkbox') {
      // Bayroq faqat YOQILADI: tashqi manba uni o'chirishga asos bermaydi.
      if (node.checked) return false;
      node.checked = true;
    } else if (entry.type === 'select') {
      if (!selectHasOption(node, value)) return false;
      node.value = value;
    } else {
      node.value = value;
    }
    node.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function applyHidden(metadata) {
    HIDDEN_KEYS.forEach(function (key) {
      var node = root.ownerDocument.querySelector('[data-mq-meta-hidden="' + key + '"]');
      if (!node) return;
      var value = metadata[key];
      if (value === null || value === undefined || value === '') return;
      node.value = String(value);
    });
    var summary = root.ownerDocument.querySelector('[data-mq-meta-hidden="metadata_source_summary"]');
    if (summary && metadata.source_summary) {
      try {
        summary.value = JSON.stringify(metadata.source_summary);
      } catch (err) {
        summary.value = '';
      }
    }
  }

  // ── Holat ko'rsatish ──────────────────────────────────────────────────────

  function setStatus(text) {
    el.status.textContent = text;
  }

  function addAlert(text, kind) {
    var node = make('p', 'mq-meta__alert' + (kind ? ' mq-meta__alert--' + kind : ''), text);
    if (kind === 'error') node.setAttribute('role', 'alert');
    el.alerts.appendChild(node);
  }

  function showPanel() {
    root.hidden = false;
  }

  function resetPanel() {
    clear(el.providers);
    clear(el.alerts);
    clear(el.rows);
    clear(el.extras);
    el.fill.hidden = true;
    el.toggle.hidden = true;
    el.details.hidden = true;
    el.toggle.setAttribute('aria-expanded', 'false');
  }

  function renderProviders(providers) {
    clear(el.providers);
    Object.keys(providers).forEach(function (name) {
      var info = providers[name];
      var modifier = 'warn';
      if (info.status === 'ok') modifier = 'ok';
      else if (info.status === 'not_configured') modifier = 'off';
      var item = make('li', 'mq-meta__provider mq-meta__provider--' + modifier);
      // Holat rang bilan EMAS, matn bilan beriladi (dizayn tizimi §5.2).
      item.appendChild(make('span', null, info.label + ' — ' + info.status_label));
      el.providers.appendChild(item);
    });
  }

  // ── Jadval ────────────────────────────────────────────────────────────────

  function renderRows(metadata, conflictsByField) {
    clear(el.rows);
    var differing = 0;
    var fillable = 0;

    FIELDS.forEach(function (entry) {
      var node = field(entry);
      if (!node) return;
      var candidate = candidateValue(entry, metadata);
      if (candidate === '') return;
      if (entry.type === 'select' && !selectHasOption(node, candidate)) {
        // Variant ro'yxatimizda yo'q — qiymat ko'rsatiladi, lekin qo'llab
        // bo'lmaydi.
        appendRow(entry, readValue(entry), candidate, false, true,
                  conflictsByField[entry.key]);
        return;
      }
      var current = readValue(entry);
      var same = compareKey(current) === compareKey(candidate);
      var empty = isEmpty(entry);
      if (!same && !empty) differing += 1;
      if (empty) fillable += 1;
      appendRow(entry, current, candidate, !same, false, conflictsByField[entry.key]);
    });

    el.toggle.hidden = !el.rows.children.length;
    el.fill.hidden = fillable === 0;
    return { differing: differing, fillable: fillable };
  }

  function appendRow(entry, current, candidate, actionable, unsupported, conflict) {
    var row = make('tr', actionable && current !== '' ? 'mq-meta__row--differs' : null);
    row.appendChild(make('th', null, entry.label)).setAttribute('scope', 'row');

    var currentCell = make('td');
    currentCell.appendChild(make(
      'span',
      'mq-meta__value' + (current === '' ? ' mq-meta__value--empty' : ''),
      current === '' ? 'bo‘sh' : displayValue(entry, current)));
    row.appendChild(currentCell);

    var candidateCell = make('td');
    candidateCell.appendChild(make('span', 'mq-meta__value',
                                   displayValue(entry, candidate)));
    if (conflict) {
      var alternatives = (conflict.alternatives || []).map(function (a) {
        return a.source + ': ' + a.value;
      }).join(' · ');
      candidateCell.appendChild(make(
        'span', 'mq-meta__source',
        'Manbalar rozi emas — ' + alternatives + '. Tekshirish tavsiya etiladi.'));
    }
    row.appendChild(candidateCell);

    var actionCell = make('td');
    if (unsupported) {
      actionCell.appendChild(make('span', 'mq-meta__source',
                                  'Ro‘yxatda yo‘q — qo‘lda tanlang'));
    } else if (!actionable) {
      actionCell.appendChild(make('span', 'mq-meta__source', 'bir xil'));
    } else {
      var button = make('button', 'ol-btn ol-btn--secondary mq-meta__apply',
                        current === '' ? 'Qo‘shish' : 'Almashtirish');
      button.type = 'button';
      button.setAttribute('aria-label',
                          entry.label + ' maydoniga manba qiymatini qo‘yish');
      button.addEventListener('click', function () {
        if (state.stale) return;
        applyField(entry, candidate);
        actionCell.replaceChildren(make('span', 'mq-meta__source', 'qo‘llandi'));
        row.classList.remove('mq-meta__row--differs');
      });
      actionCell.appendChild(button);
    }
    row.appendChild(actionCell);
    el.rows.appendChild(row);
  }

  function displayValue(entry, value) {
    if (entry.type === 'checkbox') return value === '1' ? 'ha' : 'yo‘q';
    return value.length > 300 ? value.slice(0, 300) + '…' : value;
  }

  // ── Qo'shimcha bo'limlar (faqat ko'rsatish) ───────────────────────────────

  function renderExtras(metadata) {
    clear(el.extras);

    if (metadata.authors && metadata.authors.length) {
      var group = make('div', 'mq-meta__group');
      group.appendChild(make('h4', null,
                             'Mualliflar (' + metadata.authors.length + ')'));
      var list = make('ul', 'mq-meta__list');
      metadata.authors.forEach(function (author) {
        var parts = [author.name];
        if (author.orcid) parts.push('ORCID ' + author.orcid);
        if (author.affiliation) parts.push(author.affiliation);
        parts.push('manba: ' + (author.sources || []).join('+'));
        list.appendChild(make('li', null, parts.join(' · ')));
      });
      group.appendChild(list);

      var authorsEmpty = allAuthorRowsEmpty();
      var button = make('button', 'ol-btn ol-btn--secondary mq-meta__apply',
                        authorsEmpty ? 'Mualliflarni qo‘shish'
                                     : 'Mualliflar ro‘yxatini almashtirish');
      button.type = 'button';
      button.addEventListener('click', function () {
        if (state.stale) return;
        if (!authorsEmpty &&
            !window.confirm('Formadagi mualliflar ro‘yxati o‘chirilib, '
                            + 'manbadagi ro‘yxat bilan almashtiriladi. Davom etamizmi?')) {
          return;
        }
        applyAuthors(metadata.authors);
        button.disabled = true;
        button.textContent = 'Qo‘llandi';
      });
      group.appendChild(button);
      if (!authorsEmpty) {
        group.appendChild(make('p', 'mq-meta__note',
          'Formada allaqachon muallif bor — ro‘yxat o‘z-o‘zidan almashmaydi.'));
      }
      el.extras.appendChild(group);
    }

    /* OpenAlex bergan, lekin Crossref ro'yxatiga aniq moslanmagan mualliflar.
       Ular ro'yxatga QO'SHILMAYDI — odatda bu bir odamning o'rta ismsiz
       yozilishi ("Heather A. Piwowar" / "Heather Piwowar"). Fuzzy taxmin
       qilmaymiz, lekin ma'lumotni ham yashirmaymiz. */
    if (metadata.unmatched_authors && metadata.unmatched_authors.length) {
        var extra = make('div', 'mq-meta__group');
        extra.appendChild(make('h4', null,
          'Moslanmagan mualliflar (' + metadata.unmatched_authors.length + ')'));
        var extraList = make('ul', 'mq-meta__list');
        metadata.unmatched_authors.forEach(function (author) {
          var parts = [author.name];
          if (author.orcid) parts.push('ORCID ' + author.orcid);
          if (author.affiliation) parts.push(author.affiliation);
          extraList.appendChild(make('li', null, parts.join(' · ')));
        });
        extra.appendChild(extraList);
        extra.appendChild(make('p', 'mq-meta__note',
          'Bu yozuvlar OpenAlex‘da bor, lekin Crossref ro‘yxatidagi biror '
          + 'muallifga aniq mos kelmadi. Ular ro‘yxatga avtomatik '
          + 'qo‘shilmadi — kerak bo‘lsa qo‘lda kiriting.'));
        el.extras.appendChild(extra);
    }

    if (metadata.topics && metadata.topics.length) {
      var topics = make('div', 'mq-meta__group');
      topics.appendChild(make('h4', null, 'Tashqi mavzular'));
      var topicList = make('ul', 'mq-meta__list');
      metadata.topics.forEach(function (topic) {
        topicList.appendChild(make('li', null,
          topic.name + (topic.is_primary ? ' (asosiy)' : '')));
      });
      topics.appendChild(topicList);
      topics.appendChild(make('p', 'mq-meta__note',
        'Bu OpenAlex mavzulari. OAK fan yo‘nalishi ular asosida avtomatik '
        + 'tanlanmaydi — yo‘nalishni o‘zingiz belgilaysiz.'));
      el.extras.appendChild(topics);
    }

    if (metadata.oa_status || metadata.is_oa !== null) {
      var oa = make('div', 'mq-meta__group');
      oa.appendChild(make('h4', null, 'Ochiq kirish'));
      var lines = make('ul', 'mq-meta__list');
      lines.appendChild(make('li', null,
        'Holat: ' + (metadata.oa_status || 'noma‘lum')
        + (metadata.is_oa === true ? ' (ochiq)' : metadata.is_oa === false ? ' (yopiq)' : '')));
      if (metadata.journal_is_in_doaj !== null && metadata.journal_is_in_doaj !== undefined) {
        lines.appendChild(make('li', null,
          'Jurnal DOAJ ro‘yxatida: ' + (metadata.journal_is_in_doaj ? 'ha' : 'yo‘q')));
      }
      oa.appendChild(lines);
      oa.appendChild(make('p', 'mq-meta__note',
        'Ochiq kirish holati — tashqi signal. Saytdagi "Kirish turi" huquqiy '
        + 'tushuncha va u avtomatik to‘ldirilmaydi.'));
      el.extras.appendChild(oa);
    }

    var withPdf = (metadata.external_locations || []).filter(function (loc) {
      return loc.pdf_url || loc.landing_page_url;
    });
    if (withPdf.length) {
      var locations = make('div', 'mq-meta__group');
      locations.appendChild(make('h4', null, 'Topilgan ochiq nusxalar'));
      var locationList = make('ul', 'mq-meta__list');
      withPdf.forEach(function (loc) {
        var item = make('li');
        var url = loc.pdf_url || loc.landing_page_url;
        var link = make('a', null, url);
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer nofollow';
        item.appendChild(link);
        var meta = [];
        if (loc.host_type) meta.push(loc.host_type);
        if (loc.version) meta.push(loc.version);
        if (loc.license) meta.push(loc.license);
        if (meta.length) item.appendChild(make('span', 'mq-meta__source', meta.join(' · ')));
        locationList.appendChild(item);
      });
      locations.appendChild(locationList);
      locations.appendChild(make('p', 'mq-meta__note',
        'Bu havolalar faqat ma‘lumot uchun. Fayl avtomatik yuklanmaydi — '
        + 'PDF’ni huquqini tekshirib, o‘zingiz yuklaysiz.'));
      el.extras.appendChild(locations);
    }

    if (metadata.citation_count !== null && metadata.citation_count !== undefined) {
      var citations = make('div', 'mq-meta__group');
      citations.appendChild(make('h4', null, 'Iqtiboslar'));
      citations.appendChild(make('p', 'mq-meta__list',
        (metadata.citation_count_source || 'manba') + ' bo‘yicha '
        + metadata.citation_count + ' ta iqtibos'));
      citations.appendChild(make('p', 'mq-meta__note',
        'Iqtibos soni vaqt bilan o‘zgaradi — u o‘lchangan sana bilan '
        + 'birga saqlanadi.'));
      el.extras.appendChild(citations);
    }
  }

  function allAuthorRowsEmpty() {
    var inputs = document.querySelectorAll('#mq-authors input[name="author_name"]');
    for (var i = 0; i < inputs.length; i += 1) {
      if ((inputs[i].value || '').trim()) return false;
    }
    return true;
  }

  /* Muallif qatorlari serverdan keladi; JS mavjud qatorni klonlaydi (mavjud
     `data-mq-add-author` mexanizmi bilan bir xil naqsh). */
  function applyAuthors(authors) {
    var list = document.getElementById('mq-authors');
    var rows = list.querySelectorAll('.mq-authorrow');
    if (!rows.length) return;
    var template = rows[0].cloneNode(true);
    while (list.children.length > 1) list.removeChild(list.lastChild);

    authors.forEach(function (author, index) {
      var row = index === 0 ? list.children[0] : template.cloneNode(true);
      setRowValue(row, 'author_name', author.name || '');
      setRowValue(row, 'author_orcid', author.orcid || '');
      setRowValue(row, 'author_affiliation', author.affiliation || '');
      setRowValue(row, 'author_openalex_id', author.openalex_author_id || '');
      var corresponding = row.querySelector('input[name="author_corresponding"]');
      if (corresponding) {
        corresponding.value = String(index);
        corresponding.checked = !!author.is_corresponding;
      }
      row.querySelectorAll('input[id]').forEach(function (input) {
        input.id = input.id.replace(/\d+$/, String(index));
      });
      row.querySelectorAll('label[for]').forEach(function (label) {
        label.setAttribute('for', label.getAttribute('for').replace(/\d+$/, String(index)));
      });
      if (index > 0) list.appendChild(row);
    });
  }

  function setRowValue(row, name, value) {
    var input = row.querySelector('[name="' + name + '"]');
    if (input) input.value = value;
  }

  // ── So'rov ────────────────────────────────────────────────────────────────

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  function lookup() {
    var doi = (doiInput.value || '').trim();
    if (!doi) {
      showPanel();
      resetPanel();
      setStatus('');
      addAlert('Avval DOI kiriting.', 'notice');
      doiInput.focus();
      return;
    }
    if (state.busy) return;              // ikki marta bosishdan himoya

    // Oldingi so'rov hali ketayotgan bo'lsa — bekor qilinadi.
    if (state.controller) state.controller.abort();
    var controller = new AbortController();
    state.controller = controller;
    state.busy = true;
    fetchButton.disabled = true;
    showPanel();
    resetPanel();
    setStatus('Ma‘lumotlar olinmoqda…');

    var timer = window.setTimeout(function () { controller.abort(); },
                                  REQUEST_TIMEOUT_MS);

    fetch(root.dataset.endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken()
      },
      body: JSON.stringify({ doi: doi }),
      signal: controller.signal
    }).then(function (response) {
      return response.json().then(function (body) {
        return { ok: response.ok, status: response.status, body: body };
      }).catch(function () {
        return { ok: false, status: response.status, body: {} };
      });
    }).then(function (result) {
      if (controller !== state.controller) return;   // eskirgan javob
      if (!result.ok) {
        setStatus('');
        addAlert(result.body.message
                 || 'DOI bo‘yicha ma‘lumot olinmadi.', 'error');
        return;
      }
      render(result.body, doi);
    }).catch(function (err) {
      if (controller !== state.controller) return;
      setStatus('');
      if (err && err.name === 'AbortError') {
        addAlert('So‘rov juda uzoq davom etdi va to‘xtatildi. '
                 + 'Qayta urinib ko‘ring.', 'notice');
      } else {
        addAlert('Tarmoq xatosi: ma‘lumot olinmadi.', 'error');
      }
    }).then(function () {
      window.clearTimeout(timer);
      if (controller === state.controller) {
        state.busy = false;
        fetchButton.disabled = false;
      }
    });
  }

  function render(body, requestedDoi) {
    state.data = body;
    state.doi = (doiInput.value || '').trim();
    state.stale = false;

    renderProviders(body.providers || {});

    var metadata = body.metadata || {};
    var conflictsByField = {};
    (body.conflicts || []).forEach(function (conflict) {
      conflictsByField[conflict.field] = conflict;
    });

    if (body.status === 'not_found') {
      setStatus('DOI bo‘yicha ma‘lumot topilmadi.');
    } else if (body.status === 'failed') {
      setStatus('');
    } else {
      var counts = renderRows(metadata, conflictsByField);
      renderExtras(metadata);
      var parts = [body.field_count + ' ta maydon topildi'];
      if (counts.differing) {
        parts.push(counts.differing + ' ta farq tekshirish talab qiladi');
      }
      setStatus(parts.join(' · '));
    }

    (body.warnings || []).forEach(function (warning) {
      addAlert(warning.message, warning.severity === 'notice' ? 'notice' : null);
    });

    if (body.existing_article) {
      var alert = make('p', 'mq-meta__alert mq-meta__alert--notice');
      alert.appendChild(document.createTextNode(
        'Bu DOI bilan maqola allaqachon bor: ' + body.existing_article.title + ' '));
      var link = make('a', null, 'tahrirlash');
      link.href = body.existing_article.edit_url;
      alert.appendChild(link);
      el.alerts.appendChild(alert);
    }

    if (requestedDoi !== state.doi) markStale();
  }

  function markStale() {
    if (state.stale || !state.data) return;
    state.stale = true;
    el.fill.disabled = true;
    root.querySelectorAll('.mq-meta__apply').forEach(function (button) {
      button.disabled = true;
    });
    addAlert('DOI o‘zgardi — ko‘rsatilgan ma‘lumot eskirdi. '
             + '"Ma‘lumotlarni olish" ni qayta bosing.', 'notice');
  }

  // ── Hodisalar ─────────────────────────────────────────────────────────────

  fetchButton.addEventListener('click', lookup);

  doiInput.addEventListener('input', function () {
    if (state.data && (doiInput.value || '').trim() !== state.doi) markStale();
  });

  el.toggle.addEventListener('click', function () {
    var open = el.details.hidden;
    el.details.hidden = !open;
    el.toggle.setAttribute('aria-expanded', String(open));
    el.toggle.textContent = open ? 'Topilgan ma‘lumotlarni yashirish'
                                 : 'Topilgan ma‘lumotlarni ko‘rish';
  });

  el.fill.addEventListener('click', function () {
    if (state.stale || !state.data) return;
    var metadata = state.data.metadata || {};
    var filled = 0;
    FIELDS.forEach(function (entry) {
      if (!entry.bulk || !isEmpty(entry)) return;
      if (applyField(entry, candidateValue(entry, metadata))) filled += 1;
    });
    applyHidden(metadata);
    setStatus(filled + ' ta bo‘sh maydon to‘ldirildi. To‘lgan '
              + 'maydonlar o‘zgarmadi — ularni jadvaldan alohida '
              + 'almashtirishingiz mumkin.');
    el.fill.disabled = true;
  });
})();
