/* Dissertation workspace — TOC tree, Quill editor, autosave+lock, versions,
   text-anchored annotations, review loop, notification banners.
   Vanilla JS, framework yo'q. */
(function () {
  'use strict';

  var S = window.ED_STATE || null;   // editor sahifasida to'ldiriladi

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }
  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }
  function banner(kind, html, onclose) {
    var host = document.getElementById('dw-banners');
    if (!host) return;
    var el = document.createElement('div');
    el.className = 'ed-banner ' + (kind || 'info');
    el.innerHTML = '<div>' + html + '</div><button class="dw-btn ghost" style="padding:4px 12px;">✕</button>';
    el.querySelector('button').addEventListener('click', function () {
      el.remove();
      if (onclose) onclose();
    });
    host.appendChild(el);
  }
  window.openModal = function (id) { document.getElementById(id).classList.add('show'); };
  window.closeModal = function (id) { document.getElementById(id).classList.remove('show'); };

  /* ══════════ LIST PAGE: create / invite / respond ══════════ */
  window.createProject = function () {
    postJSON('/api/dissertation/create', {
      title: document.getElementById('nd-title').value,
      degree_type: document.getElementById('nd-degree').value,
      specialty_code: document.getElementById('nd-specialty').value
    }).then(function (d) {
      if (d.success) { window.location.href = '/workspace/' + d.id + '/first'; }
      else alert(d.error || 'Xatolik');
    });
  };
  window.sendInvite = function () {
    postJSON('/api/advisor/invite', {
      username_or_email: document.getElementById('inv-ident').value,
      role: document.getElementById('inv-role').value,
      message: document.getElementById('inv-message').value
    }).then(function (d) {
      if (d.success) { alert('Taklif yuborildi ✓'); closeModal('invite-modal'); }
      else alert(d.error || 'Xatolik');
    });
  };
  window.respondInvite = function (linkId, action) {
    postJSON('/api/advisor/respond', { link_id: linkId, action: action })
      .then(function (d) {
        if (d.success) location.reload();
        else alert(d.error || 'Xatolik');
      });
  };

  /* ══════════ NOTIFICATION POLL (workspace sahifalarida, 15s) ══════════ */
  var shownNotifs = {};
  function pollNotifications() {
    fetch('/api/diss-notifications?unread=1')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        (d.notifications || []).forEach(function (n) {
          if (shownNotifs[n.id]) return;
          shownNotifs[n.id] = true;
          var p = n.payload || {};
          var link = n.dissertation_id ? '/workspace/' + n.dissertation_id + '/first' : '/workspace';
          var text = '';
          if (n.event_type === 'advisor_reviewed') text = '📋 ' + (p.message || "Rahbaringiz ishingizni ko'rib chiqdi");
          else if (n.event_type === 'student_submitted') text = '✅ Shogirdingiz tuzatishlarni kiritdi va qayta ko\'rib chiqish uchun yubordi.';
          else if (n.event_type === 'advisor_invite') { text = '👥 ' + esc(p.from || '') + ' sizga hamkorlik taklifi yubordi.'; link = '/workspace'; }
          else if (n.event_type === 'invite_accepted') text = '🤝 ' + esc(p.by || '') + ' taklifingizni qabul qildi.';
          else if (n.event_type === 'annotation_added') text = '💬 “' + esc(p.block_title || '') + '” qismiga yangi izoh qo\'shildi.';
          else if (n.event_type === 'status_changed') text = '🏷️ “' + esc(p.block_title || '') + '” holati: ' + esc(p.label || '');
          else return;
          banner(n.event_type === 'advisor_reviewed' || n.event_type === 'student_submitted' ? 'warn' : 'info',
            text + ' <a href="' + link + '" style="color:#4a9eff;font-weight:700;">Ko\'rish →</a>',
            function () { postJSON('/api/diss-notifications/mark-read', { ids: [n.id] }); });
        });
      }).catch(function () {});
  }
  if (document.getElementById('dw-banners')) {
    pollNotifications();
    setInterval(pollNotifications, 15000);
  }

  if (!S) return;   // quyidagi hammasi faqat editor sahifasi uchun

  /* ══════════ TOC TREE ══════════ */
  function statusDot(st) { return '<span class="toc-dot ' + st + '"></span>'; }
  function renderTree(nodes, host) {
    host.innerHTML = '';
    function build(items, container) {
      items.forEach(function (n) {
        var wrap = document.createElement('div');
        wrap.className = 'toc-node';
        var row = document.createElement('div');
        row.className = 'toc-row' + (n.id === S.blockId ? ' active' : '');
        row.setAttribute('data-block-id', n.id);
        row.setAttribute('draggable', S.role === 'owner' ? 'true' : 'false');
        row.innerHTML = statusDot(n.review_status) +
          '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;">' +
          esc(n.numbering) + '. ' + esc(n.title) + '</span>' +
          (n.open_annotations_count ? '<span class="toc-count">' + n.open_annotations_count + '</span>' : '') +
          (S.role === 'owner'
            ? '<span class="toc-actions">' +
              '<button title="Nomini o\'zgartirish" data-act="rename">✏️</button>' +
              (n.depth < 3 ? '<button title="Ichki qism" data-act="child">➕</button>' : '') +
              '<button title="O\'chirish" data-act="del">🗑️</button></span>'
            : '');
        row.addEventListener('click', function (e) {
          var act = e.target.getAttribute && e.target.getAttribute('data-act');
          if (act === 'rename') { e.stopPropagation(); renameBlock(n.id, n.title); return; }
          if (act === 'child') { e.stopPropagation(); addBlock(n.id); return; }
          if (act === 'del') { e.stopPropagation(); deleteBlock(n.id); return; }
          navigateToBlock(n.id);
        });
        // drag & drop reorder (owner)
        if (S.role === 'owner') {
          row.addEventListener('dragstart', function (e) {
            e.dataTransfer.setData('text/plain', String(n.id));
          });
          row.addEventListener('dragover', function (e) { e.preventDefault(); row.classList.add('drag-over'); });
          row.addEventListener('dragleave', function () { row.classList.remove('drag-over'); });
          row.addEventListener('drop', function (e) {
            e.preventDefault();
            row.classList.remove('drag-over');
            var dragged = parseInt(e.dataTransfer.getData('text/plain'), 10);
            if (!dragged || dragged === n.id) return;
            // tashlangan tugun ustiga: shu tugunning OTASIga, uning tartibidan keyin
            postJSON('/api/blocks/' + dragged + '/reorder',
                     { new_parent_id: n.parent_id || null, new_sort_order: n.sort_order + 1 })
              .then(function (d) {
                if (d.success) refreshTree();
                else alert(d.error || 'Ko\'chirishda xatolik');
              });
          });
        }
        wrap.appendChild(row);
        if (n.children && n.children.length) {
          var kids = document.createElement('div');
          kids.className = 'toc-children';
          build(n.children, kids);
          wrap.appendChild(kids);
        }
        container.appendChild(wrap);
      });
    }
    build(nodes, host);
  }
  function refreshTree() {
    fetch('/api/dissertation/' + S.dissId + '/blocks')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.success) renderTree(d.blocks, document.getElementById('toc-tree'));
      }).catch(function () {});
  }
  window.addBlock = function (parentId) {
    var title = prompt(parentId ? 'Ichki qism nomi:' : 'Yangi bob nomi:');
    if (!title) return;
    postJSON('/api/dissertation/' + S.dissId + '/blocks/create',
             { title: title, parent_id: parentId })
      .then(function (d) {
        if (d.success) refreshTree();
        else alert(d.error || 'Xatolik');
      });
  };
  function renameBlock(id, oldTitle) {
    var t = prompt('Yangi nom:', oldTitle);
    if (!t || t === oldTitle) return;
    postJSON('/api/blocks/' + id + '/rename', { title: t }).then(function (d) {
      if (d.success) { refreshTree(); if (id === S.blockId) document.getElementById('blk-title').textContent = t; }
      else alert(d.error || 'Xatolik');
    });
  }
  function deleteBlock(id) {
    if (!confirm("Ushbu qism va uning barcha ichki qismlari o'chiriladi. Davom etasizmi?")) return;
    postJSON('/api/blocks/' + id + '/delete', {}).then(function (d) {
      if (!d.success) { alert(d.error || 'Xatolik'); return; }
      if (id === S.blockId) window.location.href = '/workspace/' + S.dissId + '/first';
      else refreshTree();
    });
  }
  function navigateToBlock(id) {
    if (id === S.blockId) return;
    if (dirty && !confirm('Saqlanmagan o\'zgarishlar bor. Baribir o\'tilsinmi?')) return;
    window.location.href = '/workspace/' + S.dissId + '/edit/' + id;
  }

  /* ══════════ QUILL EDITOR ══════════ */
  var quill = new Quill('#editor-container', {
    theme: 'snow',
    readOnly: S.readOnly,
    modules: {
      toolbar: S.readOnly ? false : {
        container: [
          [{ header: [1, 2, 3, 4, false] }],
          ['bold', 'italic', 'underline', 'strike'],
          [{ list: 'ordered' }, { list: 'bullet' }],
          ['blockquote', { script: 'sub' }, { script: 'super' }],
          ['image', 'link'],
          ['clean']
        ],
        handlers: { image: imageHandler }
      }
    }
  });
  quill.root.innerHTML = S.content || '';
  applyHighlights();

  /* custom image handler → server upload */
  function imageHandler() {
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/jpeg,image/png,image/webp';
    input.onchange = function () {
      var f = input.files[0];
      if (!f) return;
      if (f.size > 5 * 1024 * 1024) { alert('Rasm hajmi 5MB dan oshmasligi kerak'); return; }
      var fd = new FormData();
      fd.append('image', f);
      fetch('/api/blocks/' + S.blockId + '/upload-image', { method: 'POST', body: fd })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.success) { alert(d.error || 'Yuklashda xatolik'); return; }
          var range = quill.getSelection(true);
          quill.insertEmbed(range ? range.index : 0, 'image', d.url);
        }).catch(function () { alert("Fayl yuklashda xatolik. Qayta urinib ko'ring."); });
    };
    input.click();
  }

  /* ══════════ AUTOSAVE + LOCK + LOCALSTORAGE DRAFT ══════════ */
  var dirty = false;
  var typingTimer = null;
  var draftKey = 'diss-draft-' + S.blockId;
  var statusEl = document.getElementById('save-status');
  var wcEl = document.getElementById('word-count');

  // lokal qoralama tiklash taklifi (saqlash uzilib qolgan bo'lsa)
  try {
    var draft = JSON.parse(localStorage.getItem(draftKey) || 'null');
    if (draft && draft.savedAt > Date.parse(S.updatedAt.replace(' ', 'T')) &&
        draft.content !== (S.content || '') && !S.readOnly) {
      if (confirm('Saqlashda uzilish bo\'lgan. Lokal nusxani tiklaysizmi?')) {
        quill.root.innerHTML = draft.content;
        dirty = true;
      } else {
        localStorage.removeItem(draftKey);
      }
    }
  } catch (e) {}

  if (!S.readOnly) {
    quill.on('text-change', function (d1, d2, source) {
      if (source !== 'user') return;
      dirty = true;
      statusEl.textContent = '● Saqlanmagan';
      clearTimeout(typingTimer);
      typingTimer = setTimeout(function () { saveBlock('autosave'); }, 3000);
      // crash xavfsizligi: lokal nusxa
      try {
        localStorage.setItem(draftKey, JSON.stringify({
          content: quill.root.innerHTML, savedAt: Date.now()
        }));
      } catch (e) {}
    });
    setInterval(function () { if (dirty) saveBlock('autosave'); }, 30000);
    // edit lock: ochilganda + 60s heartbeat, yopilganda beacon bilan unlock
    postJSON('/api/blocks/' + S.blockId + '/lock', {}).then(function (d) {
      if (d._status === 409) banner('warn', '⚠️ ' + (d.error || 'Bu qismni boshqa foydalanuvchi tahrirlamoqda'));
    });
    setInterval(function () { postJSON('/api/blocks/' + S.blockId + '/lock', {}); }, 60000);
    window.addEventListener('beforeunload', function () {
      navigator.sendBeacon && navigator.sendBeacon('/api/blocks/' + S.blockId + '/unlock', '{}');
    });
  }

  function saveBlock(saveType) {
    if (S.readOnly) return;
    statusEl.textContent = 'Saqlanmoqda…';
    postJSON('/api/blocks/' + S.blockId + '/save',
             { content: quill.root.innerHTML, save_type: saveType })
      .then(function (d) {
        if (d._status === 429) { return; }             // rate limit — jim
        if (d._status === 409) { banner('warn', '⚠️ ' + (d.error || 'Lock')); return; }
        if (!d.success && !d.unchanged) {
          statusEl.textContent = '⚠️ Saqlanmadi (qayta urinilyapti)';
          setTimeout(function () { saveBlock(saveType); }, 5000);   // retry
          return;
        }
        dirty = false;
        try { localStorage.removeItem(draftKey); } catch (e) {}
        if (d.word_count !== undefined) wcEl.textContent = d.word_count + " so'z";
        statusEl.textContent = '✓ Saqlandi ' + (d.saved_at || '');
        applyHighlights();
      })
      .catch(function () {
        statusEl.textContent = '⚠️ Tarmoq xatosi (lokal nusxa saqlanadi)';
        setTimeout(function () { saveBlock(saveType); }, 8000);
      });
  }

  /* ══════════ VERSIONS ══════════ */
  window.openVersions = function () {
    document.getElementById('ver-panel').classList.add('open');
    fetch('/api/blocks/' + S.blockId + '/versions')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var host = document.getElementById('ver-list');
        host.innerHTML = '';
        (d.versions || []).forEach(function (v) {
          var el = document.createElement('div');
          el.className = 'ver-item';
          el.innerHTML = '<b>' + esc(v.created_at) + '</b> · ' + esc(v.saved_by) +
            '<br><small style="color:#94a3b8;">' + v.save_type + ' · ' + v.word_count + ' so\'z</small>';
          el.addEventListener('click', function () { previewVersion(v.id, v.created_at); });
          host.appendChild(el);
        });
        if (!d.versions || !d.versions.length) host.innerHTML = '<p style="color:#94a3b8;">Versiyalar hali yo\'q.</p>';
      });
  };
  window.closeVersions = function () { document.getElementById('ver-panel').classList.remove('open'); };
  function previewVersion(vid, date) {
    fetch('/api/blocks/' + S.blockId + '/versions/' + vid)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) return;
        document.getElementById('ver-date').textContent = date;
        document.getElementById('ver-content').innerHTML = d.content;   // sanitized at save
        var btn = document.getElementById('ver-restore-btn');
        if (btn) btn.onclick = function () {
          if (!confirm('Joriy kontent avval alohida versiya sifatida saqlanadi. Tiklansinmi?')) return;
          postJSON('/api/blocks/' + S.blockId + '/restore/' + vid, {})
            .then(function (r2) { if (r2.success) location.reload(); else alert(r2.error || 'Xatolik'); });
        };
        openModal('ver-modal');
      });
  }

  /* ══════════ ANNOTATIONS ══════════ */
  var pendingAnchor = null;

  function plainText() { return quill.getText(); }

  // tanlangan matn ustida "Izoh qoldirish" tugmasi
  document.addEventListener('selectionchange', function () {
    var btn = document.getElementById('float-annotate');
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) { btn.style.display = 'none'; return; }
    var node = sel.anchorNode;
    var editor = document.getElementById('editor-container');
    if (!editor.contains(node)) { btn.style.display = 'none'; return; }
    var text = sel.toString().trim().slice(0, 500);
    var full = plainText();
    var idx = full.indexOf(text);
    pendingAnchor = {
      anchor_text: text,
      anchor_prefix: idx > 0 ? full.slice(Math.max(0, idx - 50), idx) : '',
      anchor_suffix: idx >= 0 ? full.slice(idx + text.length, idx + text.length + 50) : '',
      anchor_offset: idx
    };
    var rect = sel.getRangeAt(0).getBoundingClientRect();
    btn.style.display = 'block';
    btn.style.top = (window.scrollY + rect.top - 40) + 'px';
    btn.style.left = (window.scrollX + rect.left) + 'px';
  });
  window.openAnnotateForm = function () {
    if (!pendingAnchor) return;
    document.getElementById('float-annotate').style.display = 'none';
    document.getElementById('ann-selected').textContent = '“' + pendingAnchor.anchor_text.slice(0, 120) + '”';
    document.getElementById('ann-body').value = '';
    openModal('ann-modal');
  };
  window.createAnnotation = function () {
    var body = document.getElementById('ann-body').value.trim();
    if (!body || !pendingAnchor) return;
    postJSON('/api/blocks/' + S.blockId + '/annotations/create', {
      annotation_type: document.getElementById('ann-type').value,
      body: body,
      anchor_text: pendingAnchor.anchor_text,
      anchor_prefix: pendingAnchor.anchor_prefix,
      anchor_suffix: pendingAnchor.anchor_suffix,
      anchor_offset: pendingAnchor.anchor_offset
    }).then(function (d) {
      if (!d.success) { alert(d.error || 'Xatolik'); return; }
      closeModal('ann-modal');
      loadAnnotations();
      refreshTree();
    });
  };

  var annCache = [];
  function loadAnnotations() {
    fetch('/api/blocks/' + S.blockId + '/annotations')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) return;
        annCache = (d.open || []);
        renderAnnPanel(d);
        applyHighlights();
      }).catch(function () {});
  }

  function annCard(a) {
    var initial = (a.author || '?').slice(0, 1).toUpperCase();
    var replies = (a.replies || []).map(function (r) {
      return '<div class="ann-reply"><b>' + esc(r.author) + ':</b> ' + esc(r.body) +
             ' <small style="color:#64748b;">' + esc(r.created_at) + '</small></div>';
    }).join('');
    var badge = a.status === 'orphaned'
      ? '<span class="ann-type" style="background:rgba(148,163,184,0.2);color:#94a3b8;">⚠️ Matn o\'zgargan</span>'
      : '<span class="ann-type ' + a.type + '">' + esc(a.type_label) + '</span>';
    return '<div class="ann-card ' + (a.status === 'resolved' ? 'resolved' : '') + '" data-ann-id="' + a.id + '">' +
      '<div style="display:flex;align-items:center;gap:8px;">' +
      '<span class="ann-avatar">' + esc(initial) + '</span>' +
      '<b style="flex:1;">' + esc(a.author) + '</b>' + badge + '</div>' +
      '<div class="ann-anchor">“' + esc((a.anchor_text || '').slice(0, 90)) + '”</div>' +
      '<div>' + esc(a.body) + '</div>' +
      '<small style="color:#64748b;">' + esc(a.created_at) + '</small>' +
      replies +
      (a.status !== 'resolved'
        ? '<input class="ann-input" placeholder="Javob yozish… (Enter)" data-reply-to="' + a.id + '">' +
          '<div style="display:flex;gap:6px;margin-top:6px;">' +
          '<button class="dw-btn ghost" style="padding:4px 10px;font-size:0.75rem;" data-resolve="' + a.id + '">✓ Hal qilindi</button>' +
          (a.mine && !(a.replies || []).length
            ? '<button class="dw-btn ghost" style="padding:4px 10px;font-size:0.75rem;color:#f87171;" data-del="' + a.id + '">O\'chirish</button>' : '') +
          '</div>'
        : (S.role === 'advisor'
            ? '<button class="dw-btn ghost" style="padding:4px 10px;font-size:0.75rem;margin-top:6px;" data-reopen="' + a.id + '">↩️ Qayta ochish</button>' : '')) +
      '</div>';
  }

  function renderAnnPanel(d) {
    var host = document.getElementById('ann-list');
    if (!host) return;
    var html = '';
    if (d.open.length) html += d.open.map(annCard).join('');
    if (d.orphaned.length) html += '<p style="color:#94a3b8;font-size:0.75rem;margin:10px 0 6px;">MATNI O\'ZGARGANLAR</p>' + d.orphaned.map(annCard).join('');
    if (d.resolved.length) html += '<p style="color:#94a3b8;font-size:0.75rem;margin:10px 0 6px;">HAL QILINGANLAR</p>' + d.resolved.map(annCard).join('');
    host.innerHTML = html || '<p style="color:#94a3b8;font-size:0.83rem;">Hali izohlar yo\'q. Matndan parcha belgilang.</p>';
    host.querySelectorAll('[data-reply-to]').forEach(function (inp) {
      inp.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' || !inp.value.trim()) return;
        postJSON('/api/annotations/' + inp.getAttribute('data-reply-to') + '/reply',
                 { body: inp.value.trim() })
          .then(function (r) { if (r.success) loadAnnotations(); });
      });
    });
    host.querySelectorAll('[data-resolve]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        postJSON('/api/annotations/' + b.getAttribute('data-resolve') + '/resolve', {})
          .then(function () { loadAnnotations(); refreshTree(); });
      });
    });
    host.querySelectorAll('[data-reopen]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        postJSON('/api/annotations/' + b.getAttribute('data-reopen') + '/reopen', {})
          .then(function () { loadAnnotations(); });
      });
    });
    host.querySelectorAll('[data-del]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        postJSON('/api/annotations/' + b.getAttribute('data-del') + '/delete', {})
          .then(function (r) { if (r.success) loadAnnotations(); else alert(r.error || 'Xatolik'); });
      });
    });
    host.querySelectorAll('.ann-card').forEach(function (card) {
      card.addEventListener('click', function () {
        scrollToHighlight(parseInt(card.getAttribute('data-ann-id'), 10));
      });
    });
  }

  /* matn ichida sariq highlight: DOM'ga tegmasdan CSS Custom Highlight
     o'rniga oddiy usul — Quill kontentida qidirish uchun window.find emas,
     span o'rash SAQLANMAYDI (kontent ifloslanmasin): shu sabab highlight
     vaqtincha, faqat vizual — TreeWalker orqali topib Range+mark qilamiz. */
  function applyHighlights() {
    // eski vizual highlightlarni tozalash
    document.querySelectorAll('.annotation-highlight').forEach(function (el) {
      var parent = el.parentNode;
      while (el.firstChild) parent.insertBefore(el.firstChild, el);
      parent.removeChild(el);
      parent.normalize();
    });
    if (!annCache.length) return;
    var root = quill.root;
    annCache.forEach(function (a) {
      if (!a.anchor_text) return;
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      var node;
      while ((node = walker.nextNode())) {
        var idx = node.nodeValue.indexOf(a.anchor_text);
        if (idx < 0) continue;
        try {
          var range = document.createRange();
          range.setStart(node, idx);
          range.setEnd(node, idx + a.anchor_text.length);
          var span = document.createElement('span');
          span.className = 'annotation-highlight';
          span.setAttribute('data-annotation-id', a.id);
          range.surroundContents(span);
        } catch (e) { /* ko'p tugunga yoyilgan tanlov — panel orqali topiladi */ }
        break;
      }
    });
  }
  function scrollToHighlight(annId) {
    var el = document.querySelector('.annotation-highlight[data-annotation-id="' + annId + '"]');
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('pulse');
    setTimeout(function () { el.classList.remove('pulse'); }, 2200);
  }
  window.toggleAnnPanel = function () {
    document.getElementById('ann-panel').classList.toggle('mobile-open');
  };

  /* ══════════ REVIEW LOOP ══════════ */
  window.setReviewStatus = function (status) {
    if (!status) return;
    postJSON('/api/blocks/' + S.blockId + '/review-status', { status: status })
      .then(function (d) {
        if (!d.success) { alert(d.error || 'Xatolik'); return; }
        var badge = document.getElementById('blk-status');
        badge.className = 'ed-badge ' + status;
        badge.textContent = S.reviewLabels[status] || status;
        refreshTree();
      });
  };
  window.submitForReview = function () {
    if (!confirm("Ish rahbaringizga ko'rib chiqish uchun yuborilsinmi?")) return;
    postJSON('/api/dissertation/' + S.dissId + '/submit-for-review', {})
      .then(function (d) {
        if (d.success) banner('info', "📤 Ish rahbaringizga yuborildi. Javobini shu yerda ko'rasiz.");
        else alert(d.error || 'Xatolik');
      });
  };
  window.finishReview = function () {
    if (!confirm('Ko\'rib chiqish yakunlanib, shogirdga jamlangan xabar yuborilsinmi?')) return;
    postJSON('/api/dissertation/' + S.dissId + '/finish-review', {})
      .then(function (d) {
        if (d.success) banner('info', '✅ Ko\'rib chiqish yakunlandi — shogirdga xabar yuborildi.');
        else alert(d.error || 'Xatolik');
      });
  };

  /* init */
  renderTree(S.tree || [], document.getElementById('toc-tree'));
  loadAnnotations();
})();
