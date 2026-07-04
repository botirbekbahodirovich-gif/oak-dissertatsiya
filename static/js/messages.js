/* Messaging — inbox qidiruv, thread polling (5s), yuborish, fayl yuklash.
   WebSocket YO'Q (v1 qarori): 5s polling Gunicorn sync workerlarga mos. */
(function () {
  'use strict';

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }
  function fmtSize(b) {
    if (b > 1048576) return (b / 1048576).toFixed(1) + ' MB';
    if (b > 1024) return Math.round(b / 1024) + ' KB';
    return b + ' B';
  }

  /* ── inbox search ── */
  var search = document.getElementById('mx-search');
  if (search) {
    search.addEventListener('input', function () {
      var q = search.value.trim().toLowerCase();
      document.querySelectorAll('#mx-list .mx-item').forEach(function (it) {
        it.style.display = !q || (it.getAttribute('data-name') || '').indexOf(q) >= 0 ? '' : 'none';
      });
    });
  }

  /* ── navbar unread badge (har 60s) ── */
  function refreshUnread() {
    fetch('/api/messages/unread-count')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var b = document.getElementById('msg-badge');
        if (!b) return;
        var n = (d && d.unread) || 0;
        b.textContent = n > 99 ? '99+' : n;
        b.style.display = n ? 'inline-block' : 'none';
      }).catch(function () {});
  }
  if (document.getElementById('msg-badge')) {
    refreshUnread();
    setInterval(refreshUnread, 60000);
  }

  /* ── profil / boshqa sahifalardan suhbat boshlash ── */
  window.startConversation = function (userId) {
    fetch('/api/messages/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.success) window.location.href = '/messages/' + d.conversation_id;
        else alert(d.error || 'Xatolik');
      });
  };

  /* ── thread view ── */
  var T = window.MSG_STATE;
  if (!T) return;

  var msgsEl = document.getElementById('th-msgs');
  var lastId = 0;
  var firstId = null;
  var lastDay = '';

  function dayOf(ts) { return (ts || '').slice(0, 10); }

  function bubbleHtml(m) {
    var inner;
    if (m.attachment_url && m.attachment_type === 'image') {
      inner = '<img class="th-img" src="' + esc(m.attachment_url) + '" alt="" ' +
              'onclick="showLightbox(\'' + esc(m.attachment_url) + '\')">';
    } else if (m.attachment_url) {
      var icon = m.attachment_type === 'pdf' ? '📕' : m.attachment_type === 'docx' ? '📘' : '📦';
      inner = '<a class="th-file" href="' + esc(m.attachment_url) + '" download>' + icon +
              ' <span>' + esc(m.attachment_name) + '<br><small>' + fmtSize(m.attachment_size) + '</small></span></a>';
    } else {
      // xavfsizlik: matn escape qilinadi, faqat URL'lar havolaga aylantiriladi
      inner = esc(m.body).replace(/(https?:\/\/[^\s<]+)/g,
        '<a href="$1" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;">$1</a>');
    }
    return inner + '<div class="th-time">' + esc((m.created_at || '').slice(11, 16)) + '</div>';
  }

  function appendMessage(m, prepend) {
    var day = dayOf(m.created_at);
    if (!prepend && day !== lastDay) {
      var sep = document.createElement('div');
      sep.className = 'th-day';
      sep.textContent = day;
      msgsEl.appendChild(sep);
      lastDay = day;
    }
    var row = document.createElement('div');
    row.className = 'th-msg' + (m.mine ? ' mine' : '');
    row.innerHTML = '<div class="th-bubble">' + bubbleHtml(m) + '</div>';
    if (prepend) {
      msgsEl.insertBefore(row, msgsEl.children[1] || null);
    } else {
      msgsEl.appendChild(row);
    }
    if (m.id > lastId) lastId = m.id;
    if (firstId === null || m.id < firstId) firstId = m.id;
  }

  (T.initialMessages || []).forEach(function (m) { appendMessage(m); });
  msgsEl.scrollTop = msgsEl.scrollHeight;

  /* yuborish */
  var textEl = document.getElementById('th-text');
  window.sendCurrentMessage = function () {
    var body = textEl.value.trim();
    if (!body) return;
    textEl.value = '';
    fetch('/api/messages/' + T.conversationId + '/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body: body })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.success) {
          appendMessage(d.message);
          msgsEl.scrollTop = msgsEl.scrollHeight;
        } else { alert(d.error || 'Xatolik'); textEl.value = body; }
      }).catch(function () { textEl.value = body; alert('Tarmoq xatosi'); });
  };
  textEl.addEventListener('keydown', function (e) {
    // Enter = yuborish, Shift+Enter = yangi qator
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      window.sendCurrentMessage();
    }
  });

  /* fayl yuklash */
  document.getElementById('th-file').addEventListener('change', function () {
    var f = this.files[0];
    this.value = '';
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) { alert('Fayl hajmi 10MB dan oshmasligi kerak'); return; }
    var fd = new FormData();
    fd.append('file', f);
    fetch('/api/messages/' + T.conversationId + '/upload', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.success) { appendMessage(d.message); msgsEl.scrollTop = msgsEl.scrollHeight; }
        else alert(d.error || "Fayl yuklashda xatolik. Qayta urinib ko'ring.");
      }).catch(function () { alert("Fayl yuklashda xatolik. Qayta urinib ko'ring."); });
  });

  /* eski xabarlar */
  document.getElementById('load-older').addEventListener('click', function () {
    if (firstId === null) return;
    fetch('/api/messages/older/' + T.conversationId + '?before_id=' + firstId)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        (d.messages || []).slice().reverse().forEach(function (m) { appendMessage(m, true); });
        if (!(d.messages || []).length) document.getElementById('load-older').style.display = 'none';
      });
  });

  /* 5s polling — yangi xabarlar */
  setInterval(function () {
    fetch('/api/messages/' + T.conversationId + '/poll?after_id=' + lastId)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var had = false;
        (d.messages || []).forEach(function (m) {
          if (m.id > lastId) { appendMessage(m); had = true; }
        });
        if (had) msgsEl.scrollTop = msgsEl.scrollHeight;
      }).catch(function () {});
  }, 5000);

  window.showLightbox = function (src) {
    document.getElementById('lightbox-img').src = src;
    document.getElementById('lightbox').classList.add('show');
  };
})();
