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
    }).then(function (r) {
      return r.json().then(
        function (j) { j._status = r.status; j._redirected = r.redirected; return j; },
        // JSON emas (login sahifasiga redirect, proxy xato sahifasi va h.k.)
        function () { return { _status: r.status, _redirected: r.redirected, _nonjson: true }; }
      );
    });
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
  window.createInviteLink = function () {
    var roleEl = document.getElementById('inv-role');
    postJSON('/api/advisor/invite-link', { role: roleEl ? roleEl.value : 'advisor' })
      .then(function (d) {
        if (!d.success) { alert(d.error || 'Xatolik'); return; }
        var box = document.getElementById('inv-link-box');
        document.getElementById('inv-link-url').value = d.url;
        var text = 'Assalomu alaykum! Sizni Olimlar.uz dissertatsiya ish stolida hamkorlikka taklif qilaman: ';
        document.getElementById('inv-link-tg').href =
          'https://t.me/share/url?url=' + encodeURIComponent(d.url) + '&text=' + encodeURIComponent(text);
        document.getElementById('inv-link-mail').href =
          'mailto:?subject=' + encodeURIComponent('Olimlar.uz — hamkorlik taklifi') +
          '&body=' + encodeURIComponent(text + d.url);
        box.style.display = 'block';
      });
  };
  window.copyInviteLink = function () {
    var el = document.getElementById('inv-link-url');
    if (!el) return;
    el.select();
    var done = function () { alert('Havola nusxalandi ✓'); };
    if (navigator.clipboard) navigator.clipboard.writeText(el.value).then(done, function () { document.execCommand('copy'); done(); });
    else { document.execCommand('copy'); done(); }
  };
  window.messageUser = function (userId) {
    if (!userId) return;
    postJSON('/api/messages/start', { user_id: userId })
      .then(function (d) {
        if (d.success) window.location.href = '/messages/' + d.conversation_id;
        else alert(d.error || 'Xatolik');
      });
  };

  /* ══════════ COLLABORATORS (owner boshqaruvi) ══════════ */
  var _collabDiss = null;
  window.openCollaborators = function (dissId) {
    _collabDiss = dissId;
    var box = document.getElementById('collab-link-box');
    if (box) box.style.display = 'none';
    openModal('collab-modal');
    loadCollaborators();
  };
  function loadCollaborators() {
    fetch('/api/dissertation/' + _collabDiss + '/collaborators')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var host = document.getElementById('collab-list');
        if (!host) return;
        if (!d.success) { host.innerHTML = ''; return; }
        if (!d.collaborators.length) {
          host.innerHTML = '<p style="color:#94a3b8;font-size:0.83rem;">Hali hamkorlar yo\'q.</p>';
          return;
        }
        host.innerHTML = d.collaborators.map(function (c) {
          var perm = function (key, label) {
            return '<label style="font-size:0.78rem;margin-right:10px;white-space:nowrap;">' +
              '<input type="checkbox" style="width:auto;margin:0 3px 0 0;" ' +
              (c[key] ? 'checked' : '') + (c.status !== 'accepted' ? ' disabled' : '') +
              ' onchange="toggleCollabPerm(' + c.id + ',\'' + key + '\',this.checked)"> ' + label + '</label>';
          };
          return '<div style="border:1px solid rgba(148,163,184,0.18);border-radius:10px;padding:9px 11px;margin-bottom:8px;">' +
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">' +
            '<b>' + esc(c.username) + '</b>' +
            '<span style="font-size:0.72rem;color:#94a3b8;">' +
            (c.status === 'accepted' ? '✓ qabul qildi' : '⏳ kutilmoqda') + '</span></div>' +
            '<div style="margin-top:8px;display:flex;flex-wrap:wrap;align-items:center;">' +
            perm('can_comment', 'Izoh') + perm('can_edit', 'Tahrir') + perm('can_review_status', 'Baholash') +
            '<button class="dw-btn ghost" style="padding:3px 10px;font-size:0.74rem;color:#f87171;margin-left:auto;" onclick="removeCollaborator(' + c.id + ')">O\'chirish</button>' +
            '</div></div>';
        }).join('');
      }).catch(function () {});
  }
  function collabPerms() {
    return {
      can_comment: document.getElementById('collab-comment').checked,
      can_edit: document.getElementById('collab-edit').checked,
      can_review_status: document.getElementById('collab-review').checked
    };
  }
  window.inviteCollaborator = function () {
    var body = collabPerms();
    body.username_or_email = document.getElementById('collab-ident').value;
    postJSON('/api/dissertation/' + _collabDiss + '/collaborators/invite', body)
      .then(function (d) {
        if (!d.success) { alert(d.error || 'Xatolik'); return; }
        document.getElementById('collab-ident').value = '';
        loadCollaborators();
      });
  };
  window.createCollabLink = function () {
    postJSON('/api/dissertation/' + _collabDiss + '/collaborators/invite-link', collabPerms())
      .then(function (d) {
        if (!d.success) { alert(d.error || 'Xatolik'); return; }
        document.getElementById('collab-link-url').value = d.url;
        document.getElementById('collab-link-box').style.display = 'block';
      });
  };
  window.toggleCollabPerm = function (cid, key, val) {
    var body = {}; body[key] = val;
    postJSON('/api/collaborators/' + cid + '/permissions', body)
      .then(function (d) { if (!d.success) { alert(d.error || 'Xatolik'); loadCollaborators(); } });
  };
  window.removeCollaborator = function (cid) {
    if (!confirm('Ushbu hamkor loyihadan olib tashlansinmi?')) return;
    postJSON('/api/collaborators/' + cid + '/remove', {})
      .then(function (d) { if (d.success) loadCollaborators(); else alert(d.error || 'Xatolik'); });
  };
  window.respondCollaborator = function (collabId, action) {
    postJSON('/api/collaborators/respond', { collab_id: collabId, action: action })
      .then(function (d) { if (d.success) location.reload(); else alert(d.error || 'Xatolik'); });
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
          var markRead = function () { postJSON('/api/diss-notifications/mark-read', { ids: [n.id] }); };
          var accBtn = function (fn, id) {
            return '<button class="dw-btn" style="border:none;background:linear-gradient(135deg,#3b82f6,#7c5cfc);color:#fff;font-weight:700;padding:4px 12px;border-radius:8px;cursor:pointer;margin-left:6px;" onclick="' + fn + '(' + id + ',\'accept\')">✅ Qabul qilish</button> ' +
              '<button class="dw-btn ghost" style="background:rgba(148,163,184,0.15);border:1px solid rgba(148,163,184,0.25);color:inherit;padding:4px 12px;border-radius:8px;cursor:pointer;" onclick="' + fn + '(' + id + ',\'decline\')">❌ Rad etish</button>';
          };
          // rahbar/shogird taklifi — bevosita tugmalar bilan
          if (n.event_type === 'advisor_invite' && p.link_id) {
            banner('info', '👥 <b>' + esc(p.from || '') + '</b> sizni hamkorlikka taklif qilmoqda. ' +
              accBtn('respondInvite', p.link_id), markRead);
            return;
          }
          // qo'shimcha hamkor taklifi — bevosita tugmalar bilan
          if (n.event_type === 'collaborator_invite' && p.collab_id) {
            banner('info', '🤝 <b>' + esc(p.from || '') + '</b> sizni "' + esc(p.diss_title || 'loyiha') +
              '" ga hamkor qilib taklif qilmoqda. ' + accBtn('respondCollaborator', p.collab_id), markRead);
            return;
          }
          var link = n.dissertation_id ? '/workspace/' + n.dissertation_id + '/first' : '/workspace';
          var text = '';
          if (n.event_type === 'advisor_reviewed') text = '📋 ' + (p.message || "Rahbaringiz ishingizni ko'rib chiqdi");
          else if (n.event_type === 'student_submitted') text = '✅ Shogirdingiz tuzatishlarni kiritdi va qayta ko\'rib chiqish uchun yubordi.';
          else if (n.event_type === 'advisor_invite') { text = '👥 ' + esc(p.from || '') + ' sizga hamkorlik taklifi yubordi.'; link = '/workspace'; }
          else if (n.event_type === 'invite_accepted') text = '🤝 ' + esc(p.by || '') + ' taklifingizni qabul qildi.';
          else if (n.event_type === 'annotation_added') text = '💬 “' + esc(p.block_title || '') + '” qismiga yangi izoh qo\'shildi.';
          else if (n.event_type === 'status_changed') text = '🏷️ “' + esc(p.block_title || '') + '” holati: ' + esc(p.label || '');
          else if (n.event_type === 'new_message') { text = '✉️ ' + esc(p.from || 'Yangi') + ': ' + esc(p.snippet || 'xabar'); link = p.conversation_id ? '/messages/' + p.conversation_id : '/messages'; }
          else return;
          banner(n.event_type === 'advisor_reviewed' || n.event_type === 'student_submitted' ? 'warn' : 'info',
            text + ' <a href="' + link + '" style="color:#4a9eff;font-weight:700;">Ko\'rish →</a>',
            markRead);
        });
      }).catch(function () {});
  }
  if (document.getElementById('dw-banners')) {
    pollNotifications();
    setInterval(pollNotifications, 15000);
  }

  if (!S) return;   // quyidagi hammasi faqat editor sahifasi uchun

  // YAGONA MANBA: joriy blok id. Blok almashtirish/yaratish/yopishdan OLDIN
  // eski blok saqlanadi, keyingina o'zgaradi (kontent aralashib ketmasligi uchun).
  var currentBlockId = S.blockId;

  // "I BOB", "1-bob" kabi sarlavhalar allaqachon raqamli — ikki karra
  // raqamlamaymiz (server _heading_label bilan bir xil qoida).
  var BOB_RE = /^\s*(?:[ivxlcdm]+|\d+)\s*[-.–\s]*bob\b/i;
  function blockLabel(numbering, title) {
    title = title || '';
    if (numbering && !BOB_RE.test(title)) return numbering + '. ' + title;
    return title;
  }

  /* ══════════ TOC TREE ══════════ */
  function statusDot(st) { return '<span class="toc-dot ' + st + '"></span>'; }
  function renderTree(nodes, host) {
    host.innerHTML = '';
    function build(items, container) {
      items.forEach(function (n) {
        var wrap = document.createElement('div');
        wrap.className = 'toc-node';
        var row = document.createElement('div');
        var special = !!n.is_special;
        row.className = 'toc-row' + (n.id === currentBlockId ? ' active' : '') +
          (special ? ' special' : '') + (n.depth === 0 ? ' depth-0' : '');
        row.setAttribute('data-block-id', n.id);
        row.setAttribute('draggable', S.role === 'owner' ? 'true' : 'false');
        var showNum = n.numbering && !special && !BOB_RE.test(n.title || '');
        row.innerHTML = statusDot(n.review_status) +
          (showNum ? '<span class="toc-num">' + esc(n.numbering) + '.</span>' : '') +
          '<span class="toc-title">' + esc(n.title) + '</span>' +
          (n.open_annotations_count ? '<span class="toc-count">' + n.open_annotations_count + '</span>' : '') +
          (S.role === 'owner'
            ? '<span class="toc-actions">' +
              '<button title="Nomini o\'zgartirish" data-act="rename">✏️</button>' +
              '<button title="' + (special ? 'Raqamlansin' : 'Raqamlanmasin (Kirish, Xulosa kabi)') +
                '" data-act="toggle-num">' + (special ? '🔢' : '🚫') + '</button>' +
              (n.depth < 3 ? '<button title="Ichki qism" data-act="child">➕</button>' : '') +
              '<button title="O\'chirish" data-act="del">🗑️</button></span>'
            : '');
        row.addEventListener('click', function (e) {
          var act = e.target.getAttribute && e.target.getAttribute('data-act');
          if (act === 'rename') { e.stopPropagation(); renameBlock(n.id, n.title); return; }
          if (act === 'toggle-num') { e.stopPropagation(); toggleNumbering(n); return; }
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
        if (!d.success) return;
        renderTree(d.blocks, document.getElementById('toc-tree'));
        updateCurrentHeading(d.blocks);   // raqamlash siljisa sarlavha yangilansin
      }).catch(function () {});
  }
  function updateCurrentHeading(nodes) {
    var found = null;
    (function scan(items) {
      (items || []).forEach(function (n) {
        if (n.id === currentBlockId) found = n;
        if (n.children) scan(n.children);
      });
    })(nodes);
    var h = document.getElementById('blk-heading');
    if (found && h) h.textContent = found.is_special ? (found.title || '')
                                                     : blockLabel(found.numbering, found.title);
  }
  function toggleNumbering(n) {
    postJSON('/api/blocks/' + n.id + '/rename',
             { title: n.title, is_special: !n.is_special })
      .then(function (d) { if (d.success) refreshTree(); else alert(d.error || 'Xatolik'); });
  }
  window.addBlock = function (parentId) {
    var title = prompt(parentId ? 'Ichki qism nomi:' : 'Yangi bob nomi:');
    if (title == null) return;
    title = title.trim();
    if (!title) return;
    // yangi blok yaratishdan OLDIN joriy blokni saqlaymiz (kontent aralashmasin)
    flushThen(function () {
      postJSON('/api/dissertation/' + S.dissId + '/blocks/create',
               { title: title, parent_id: parentId })
        .then(function (d) {
          if (d.success) refreshTree();
          else alert(d.error || 'Xatolik');
        });
    });
  };
  function renameBlock(id, oldTitle) {
    var t = prompt('Yangi nom:', oldTitle);
    if (t == null) return;
    t = t.trim();
    if (!t || t === oldTitle) return;
    postJSON('/api/blocks/' + id + '/rename', { title: t }).then(function (d) {
      if (d.success) refreshTree();   // sarlavha + raqamlash refreshTree'da yangilanadi
      else alert(d.error || 'Xatolik');
    });
  }
  function deleteBlock(id) {
    if (!confirm("Ushbu qism va uning barcha ichki qismlari o'chiriladi. Davom etasizmi?")) return;
    postJSON('/api/blocks/' + id + '/delete', {}).then(function (d) {
      if (!d.success) { alert(d.error || 'Xatolik'); return; }
      if (id === currentBlockId) window.location.href = '/workspace/' + S.dissId + '/first';
      else refreshTree();
    });
  }
  function navigateToBlock(id) {
    if (id === currentBlockId) return;
    clearTimeout(typingTimer);
    var go = function () { window.location.href = '/workspace/' + S.dissId + '/edit/' + id; };
    if (S.readOnly || !dirty) { go(); return; }
    // eski blok uchun oxirgi saqlash (manual — rate-limitdan xoli), keyin o'tamiz
    persist('manual').then(function (ok) {
      if (ok) { go(); return; }
      if (confirm('Saqlab bo\'lmadi — o\'zgarishlar lokal nusxada saqlangan. Baribir o\'tilsinmi?')) go();
    });
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
  /* Vizual annotatsiya-highlight spanlari KONTENT EMAS — saqlashdan oldin
     olib tashlanadi. Aks holda ular bazaga yozilib qoladi va DOM mutatsiyasi
     Quill'da source='user' text-change chiqarib, cheksiz autosave halqasi +
     20 talik versiya tarixini yuvib yuborishga olib keladi. */
  function unwrapHighlights(root) {
    root.querySelectorAll('span.annotation-highlight').forEach(function (el) {
      var parent = el.parentNode;
      while (el.firstChild) parent.insertBefore(el.firstChild, el);
      parent.removeChild(el);
      parent.normalize();
    });
  }
  function stripHighlightsHTML(html) {
    html = html || '';
    if (html.indexOf('annotation-highlight') === -1) return html;
    var tmp = document.createElement('div');
    tmp.innerHTML = html;
    unwrapHighlights(tmp);
    return tmp.innerHTML;
  }
  // saqlanadigan "toza" kontent — highlight spanlarisiz
  function getCleanContent() {
    if (!quill.root.querySelector('.annotation-highlight')) return quill.root.innerHTML;
    var tmp = quill.root.cloneNode(true);
    unwrapHighlights(tmp);
    return tmp.innerHTML;
  }

  // Kontent yuklash: eski saqlashlarda qolib ketgan highlight spanlarini
  // tozalaymiz; 'silent' update — Quill normalizatsiyasi darhol o'tsin va
  // text-change ('user') chiqmasin (aks holda ochilgan zahoti "dirty").
  quill.root.innerHTML = stripHighlightsHTML(S.content || '');
  quill.update('silent');

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
  var saving = false;
  var typingTimer = null;
  var draftKey = 'diss-draft-' + currentBlockId;
  var statusEl = document.getElementById('save-status');
  var wcEl = document.getElementById('word-count');

  // Quill normalizatsiyasidan KEYINGI "serverdagi holat" nusxasi — dirty va
  // qoralama solishtiruvlari S.content xom satriga emas, SHU nusxaga
  // nisbatan (normalizatsiya farqlari soxta "dirty" bermasligi uchun).
  var lastSavedContent = getCleanContent();

  // "1 240 / 6 000 so'z" — word_target bo'lsa (Akademik Reja OAK skeleti)
  function fmtWords(n) {
    var f = function (x) { return String(x).replace(/\B(?=(\d{3})+(?!\d))/g, ' '); };
    return S.wordTarget ? f(n) + ' / ' + f(S.wordTarget) + " so'z" : n + " so'z";
  }

  function setStatus(kind, text) {
    if (!statusEl) return;
    statusEl.className = kind || '';
    statusEl.textContent = text;
  }
  function hm() {
    var d = new Date();
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }
  // qisqa kontent-barmoq izi (djb2) — qoralama qaysi server holatidan
  // tarmoqlanganini aniqlash uchun (soat/timezone farqlariga bog'liq EMAS:
  // eski usul naive UTC satrini lokal vaqt deb o'qib, O'zbekistonda 5 soatlik
  // siljish bilan ESKI qoralamani yangi server matni ustiga taklif qilardi)
  function hashStr(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return h + ':' + s.length;
  }
  function saveDraft() {
    try {
      localStorage.setItem(draftKey, JSON.stringify({
        content: getCleanContent(), savedAt: Date.now(),
        base: hashStr(lastSavedContent)
      }));
    } catch (e) {}
  }

  // 14 kundan eski qoralamalar tozalanadi (localStorage to'lib qolmasin)
  try {
    var cutoff = Date.now() - 14 * 864e5;
    Object.keys(localStorage).forEach(function (k) {
      if (k.indexOf('diss-draft-') !== 0) return;
      var v = JSON.parse(localStorage.getItem(k) || 'null');
      if (!v || !v.savedAt || v.savedAt < cutoff) localStorage.removeItem(k);
    });
  } catch (e) {}

  // Lokal qoralama tiklash taklifi (saqlash uzilib qolgan bo'lsa).
  // Ishonchli belgi — base hash: qoralama AYNAN hozirgi server holatidan
  // tarmoqlangan bo'lsa, u serverdagidan yangi. Server holati boshqacha
  // bo'lsa (boshqa qurilma/tab saqlagan) — qoralama eskirgan, o'chiriladi.
  // savedAt vs updatedAtMs (epoch, server beradi) — eski formatdagi
  // qoralamalar uchun zaxira yo'l.
  try {
    var draft = JSON.parse(localStorage.getItem(draftKey) || 'null');
    if (draft && !S.readOnly && typeof draft.content === 'string' &&
        draft.content !== lastSavedContent) {
      var sameBase = draft.base != null && draft.base === hashStr(lastSavedContent);
      var legacyNewer = draft.base == null && draft.savedAt &&
                        draft.savedAt > (S.updatedAtMs || 0) + 5000;
      if (sameBase || legacyNewer) {
        if (confirm('Saqlashda uzilish bo\'lgan. Lokal nusxani tiklaysizmi?')) {
          quill.root.innerHTML = draft.content;
          quill.update('silent');
          dirty = true;
          setStatus('dirty', '● Saqlanmagan');
          typingTimer = setTimeout(function () { persist('autosave'); }, 1500);
        } else {
          localStorage.removeItem(draftKey);
        }
      } else {
        localStorage.removeItem(draftKey);   // eskirgan/boshqa holatdan qolgan
      }
    }
  } catch (e) {}

  var failWarned = false;
  function warnSaveFailed() {
    if (failWarned) return;
    failWarned = true;
    banner('warn', '⚠️ Server bilan aloqa uzildi — matningiz brauzerda lokal ' +
      'saqlanmoqda. Internet tiklangach 💾 Saqlash tugmasini bosing.');
  }
  var sessionWarned = false;
  function sessionExpired() {
    saveDraft();
    setStatus('error', '⚠ Saqlanmadi — sessiya tugagan');
    if (sessionWarned) return;
    sessionWarned = true;
    banner('warn', '⚠️ Sessiya muddati tugagan — matningiz brauzerda lokal saqlanmoqda. ' +
      '<a href="/login" target="_blank" style="color:#4a9eff;font-weight:700;">Yangi oynada qayta kiring</a>, ' +
      "so'ng bu yerda 💾 Saqlash tugmasini bosing.");
  }

  /* Saqlash — Promise qaytaradi (true=saqlandi/o'zgarmagan, false=muvaffaqiyatsiz).
     Kontent oxirgi saqlanganidek bo'lsa tarmoqqa chiqmaydi. Faqat 'autosave'
     5s rate-limitga tushadi; 'manual' har doim yoziladi. Xato bo'lsa 3 marta
     eksponensial kutish bilan qayta uriniladi; lokal nusxa saqlanib turadi. */
  function persist(saveType, attempt) {
    if (S.readOnly) return Promise.resolve(true);
    var content = getCleanContent();
    if (content === lastSavedContent) {                // o'zgarish yo'q
      dirty = false;
      try { localStorage.removeItem(draftKey); } catch (e) {}
      if (saveType === 'manual') setStatus('saved', '✓ Saqlandi ' + hm());
      return Promise.resolve(true);
    }
    attempt = attempt || 1;
    saving = true;
    setStatus('saving', 'Saqlanmoqda…');
    return postJSON('/api/blocks/' + currentBlockId + '/save',
             { content: content, save_type: saveType })
      .then(function (d) {
        saving = false;
        if (d._status === 429) {                       // autosave rate-limit — jim
          setStatus('dirty', '● Saqlanmagan');
          clearTimeout(typingTimer);
          typingTimer = setTimeout(function () { if (dirty) persist('autosave'); }, 5500);
          return true;                                 // dirty saqlanadi, keyin yoziladi
        }
        if (d._status === 409) {
          banner('warn', '⚠️ ' + (d.error || 'Bu qismni boshqa foydalanuvchi tahrirlamoqda'));
          setStatus('error', '⚠ Saqlanmadi — qism qulflangan');
          return false;
        }
        if (d._nonjson) {                              // JSON o'rniga sahifa keldi
          if (d._redirected || d._status === 401) { sessionExpired(); return false; }
          throw new Error('non-json response');
        }
        if (!d.success && !d.unchanged) throw new Error(d.error || 'save failed');
        lastSavedContent = content;
        // so'rov uchayotgan paytda terilgan matn yo'qolmasin — qayta tekshiruv
        dirty = getCleanContent() !== lastSavedContent;
        if (dirty) {
          clearTimeout(typingTimer);
          typingTimer = setTimeout(function () { persist('autosave'); }, 3000);
        } else {
          try { localStorage.removeItem(draftKey); } catch (e) {}
        }
        if (d.word_count !== undefined && wcEl) wcEl.textContent = fmtWords(d.word_count);
        setStatus('saved', '✓ Saqlandi ' + hm());      // mijoz vaqti (server UTC'da)
        applyHighlights();
        return true;
      })
      .catch(function () {
        saving = false;
        saveDraft();                                   // lokal nusxa kafolatlanadi
        if (attempt < 3) {
          setStatus('error', '⚠ Saqlanmadi — qayta urinilmoqda (' + attempt + '/3)');
          return new Promise(function (res) {
            setTimeout(function () { res(persist(saveType, attempt + 1)); },
                       1000 * Math.pow(2, attempt));   // 2s, 4s
          });
        }
        setStatus('error', '⚠ Saqlanmadi — matn lokal nusxada');
        warnSaveFailed();
        return false;
      });
  }

  // joriy blokni saqlab, keyin cb() — yangi blok yaratish/almashtirishdan oldin
  function flushThen(cb) {
    clearTimeout(typingTimer);
    if (S.readOnly || !dirty) { cb(); return; }
    persist('manual').then(function () { cb(); });
  }

  // qo'lda "💾 Saqlash" — debounce'ni chetlab, darhol yozadi (rate-limitsiz)
  window.forceSave = function () {
    clearTimeout(typingTimer);
    persist('manual');
  };
  // Ctrl/Cmd+S — qo'lda saqlash
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      if (!S.readOnly) window.forceSave();
    }
  });

  /* Word/skrinshotdan qo'yilgan rasmlar data: URI bo'lib keladi — server
     sanitizatsiyasi (bleach, protokol http/https) ularni OLIB TASHLAYDI:
     muharrirda ko'rinadi, refresh'dan keyin "yo'qoladi". Shu sabab ularni
     darhol serverga yuklab, URL bilan almashtiramiz. */
  function dataURItoBlob(uri) {
    var m = uri.match(/^data:([^;,]+)?((?:;[^;,]*)*),([\s\S]*)$/);
    if (!m) return null;
    try {
      var bytes;
      if (/;base64/i.test(m[2] || '')) {
        var bin = atob(m[3]);
        bytes = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      } else {
        bytes = new TextEncoder().encode(decodeURIComponent(m[3]));
      }
      return new Blob([bytes], { type: m[1] || 'image/png' });
    } catch (e) { return null; }
  }
  var imgWarned = false;
  function dropDataImage(src, msg) {
    quill.root.querySelectorAll('img[src^="data:"]').forEach(function (el) {
      if (el.getAttribute('src') === src) el.remove();
    });
    quill.update('silent');
    if (!imgWarned) {
      imgWarned = true;
      banner('warn', '⚠️ ' + (msg ||
        "Qo'yilgan rasmni serverga yuklab bo'lmadi, shu sabab u matnga qo'shilmadi. " +
        'Faqat JPG/PNG/WEBP (5MB gacha) qabul qilinadi — rasmni panel ustidagi 🖼 tugmasi orqali yuklang.'));
    }
  }
  var imgUploading = false;
  function uploadDataImages() {
    if (imgUploading || S.readOnly) return;
    var img = quill.root.querySelector('img[src^="data:"]');
    if (!img) return;
    var src = img.getAttribute('src');
    var blob = dataURItoBlob(src);
    if (!blob) { dropDataImage(src); return; }
    if (blob.size > 5 * 1024 * 1024) { dropDataImage(src, 'Rasm hajmi 5MB dan oshmasligi kerak'); return; }
    imgUploading = true;
    var fd = new FormData();
    var ext = (blob.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
    fd.append('image', blob, 'paste.' + ext);
    fetch('/api/blocks/' + currentBlockId + '/upload-image', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) { dropDataImage(src, d.error); return; }
        quill.root.querySelectorAll('img[src^="data:"]').forEach(function (el) {
          if (el.getAttribute('src') === src) el.setAttribute('src', d.url);
        });
        quill.update('silent');
        dirty = true;                                  // URL'li holat saqlansin
        clearTimeout(typingTimer);
        typingTimer = setTimeout(function () { persist('autosave'); }, 1200);
      })
      .catch(function () { dropDataImage(src); })
      .then(function () { imgUploading = false; uploadDataImages(); });
  }

  if (!S.readOnly) {
    quill.on('text-change', function (d1, d2, source) {
      if (source !== 'user') return;
      uploadDataImages();
      // applyHighlights kabi programmatik DOM mutatsiyalarini Quill 'user'
      // deb belgilaydi — haqiqiy o'zgarishni kontent bo'yicha tekshiramiz
      if (getCleanContent() === lastSavedContent) return;
      dirty = true;
      setStatus('dirty', '● Saqlanmagan');
      clearTimeout(typingTimer);
      typingTimer = setTimeout(function () { persist('autosave'); }, 3000);
      saveDraft();                                     // crash xavfsizligi
    });
    setInterval(function () { if (dirty && !saving) persist('autosave'); }, 30000);
    // edit lock: ochilganda + 60s heartbeat, yopilganda beacon bilan unlock
    postJSON('/api/blocks/' + currentBlockId + '/lock', {}).then(function (d) {
      if (d._status === 409) banner('warn', '⚠️ ' + (d.error || 'Bu qismni boshqa foydalanuvchi tahrirlamoqda'));
    });
    setInterval(function () { postJSON('/api/blocks/' + currentBlockId + '/lock', {}); }, 60000);
    // sahifadan chiqishda: saqlanmagan bo'lsa AVVAL beacon bilan saqlaymiz,
    // KEYIN qulfni ochamiz (saqlash qulfni yangilaydi) va brauzer tasdiq
    // oynasini chiqaramiz
    window.addEventListener('beforeunload', function (e) {
      var content = getCleanContent();
      var isDirty = content !== lastSavedContent;
      if (isDirty) {
        saveDraft();
        try {
          var blob = new Blob(
            [JSON.stringify({ content: content, save_type: 'manual' })],
            { type: 'application/json' });
          if (navigator.sendBeacon) navigator.sendBeacon('/api/blocks/' + currentBlockId + '/save', blob);
        } catch (e2) {}
      }
      if (navigator.sendBeacon) navigator.sendBeacon('/api/blocks/' + currentBlockId + '/unlock', '{}');
      if (isDirty) {
        e.preventDefault();
        e.returnValue = '';                            // brauzer tasdiq oynasi
        return '';
      }
    });
    uploadDataImages();   // tiklangan qoralamada data: rasm bo'lishi mumkin
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
