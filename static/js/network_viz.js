/* ============================================================
 * Scientific Network Visualization Engine (dual-mode).
 *  Mode B (default): canvas force-directed graph — self-contained, no external
 *    libs, grid-bucketed repulsion for large arrays.
 *  Mode A: geographic map — uses Leaflet + MarkerCluster if present on the page,
 *    otherwise degrades gracefully with a notice.
 *
 * Semantic edge colors:  green = mentorship, blue = institutional, red = co-author.
 * Controls: specialization filter, timeline slider (2022–2026), mode switch,
 *           "My Network" ego isolation for authenticated users.
 *
 * Usage:  NetworkViz.mount('#network', { egoId: 12, canMyNetwork: true });
 * ============================================================ */
(function (global) {
  'use strict';

  var EDGE_COLORS = { green: '#22c55e', blue: '#3b82f6', red: '#ef4444' };
  var YEAR_MIN = 2022, YEAR_MAX = 2026;

  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) { if (attrs.hasOwnProperty(k)) e.setAttribute(k, attrs[k]); }
    if (html != null) e.innerHTML = html;
    return e;
  }

  function NetworkViz(container, opts) {
    this.container = container;
    this.opts = opts || {};
    this.mode = 'graph';
    this.spec = '';
    this.yearFrom = YEAR_MIN;
    this.yearTo = YEAR_MAX;
    this.ego = null;
    this.data = { nodes: [], edges: [] };
    this._raf = null;
    this._buildUI();
    this.reload();
  }

  NetworkViz.prototype._buildUI = function () {
    var self = this;
    this.container.innerHTML = '';
    this.container.classList.add('netviz');

    var bar = el('div', { class: 'netviz-toolbar' });

    // Mode switch
    var modeBtn = el('button', { class: 'netviz-btn', type: 'button' }, '🌐 Xarita');
    modeBtn.addEventListener('click', function () {
      self.mode = (self.mode === 'graph') ? 'map' : 'graph';
      modeBtn.innerHTML = (self.mode === 'graph') ? '🌐 Xarita' : '🕸 Graf';
      self._renderMode();
    });
    bar.appendChild(modeBtn);

    // Specialization filter
    var spec = el('input', { class: 'netviz-spec', type: 'text',
      placeholder: 'Ixtisoslik (05.01.01)' });
    spec.addEventListener('change', function () {
      self.spec = spec.value.trim();
      self.reload();
    });
    bar.appendChild(spec);

    // Timeline range slider (from / to)
    var yf = el('input', { class: 'netviz-year', type: 'range',
      min: YEAR_MIN, max: YEAR_MAX, value: YEAR_MIN, step: 1 });
    var yt = el('input', { class: 'netviz-year', type: 'range',
      min: YEAR_MIN, max: YEAR_MAX, value: YEAR_MAX, step: 1 });
    var ylab = el('span', { class: 'netviz-ylab' }, YEAR_MIN + '–' + YEAR_MAX);
    function onYear() {
      self.yearFrom = Math.min(+yf.value, +yt.value);
      self.yearTo = Math.max(+yf.value, +yt.value);
      ylab.textContent = self.yearFrom + '–' + self.yearTo;
      self._applyYearFilter();
      self._renderMode();
    }
    yf.addEventListener('input', onYear);
    yt.addEventListener('input', onYear);
    bar.appendChild(yf); bar.appendChild(yt); bar.appendChild(ylab);

    // "My Network" ego shortcut (authenticated users)
    if (this.opts.canMyNetwork && this.opts.egoId) {
      var myBtn = el('button', { class: 'netviz-btn netviz-my', type: 'button' },
        '👤 Mening tarmog\'im');
      myBtn.addEventListener('click', function () {
        self.ego = self.ego ? null : self.opts.egoId;
        myBtn.classList.toggle('is-active', !!self.ego);
        self.reload();
      });
      bar.appendChild(myBtn);
    }

    // Legend
    var legend = el('div', { class: 'netviz-legend' },
      '<span style="color:' + EDGE_COLORS.green + '">● ustoz–shogird</span> ' +
      '<span style="color:' + EDGE_COLORS.blue + '">● muassasa</span> ' +
      '<span style="color:' + EDGE_COLORS.red + '">● hammualliflik</span>');
    bar.appendChild(legend);

    this.container.appendChild(bar);

    this.stage = el('div', { class: 'netviz-stage' });
    this.stage.style.position = 'relative';
    this.container.appendChild(this.stage);

    this.canvas = el('canvas', { class: 'netviz-canvas' });
    this.stage.appendChild(this.canvas);
    this._bindCanvas();
  };

  NetworkViz.prototype.reload = function () {
    var self = this;
    var qs = ['year_from=' + this.yearFrom, 'year_to=' + this.yearTo];
    if (this.spec) qs.push('spec=' + encodeURIComponent(this.spec));
    if (this.ego) qs.push('ego=' + this.ego);
    this.stage.classList.add('is-loading');
    fetch('/api/v1/network?' + qs.join('&'))
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        self.stage.classList.remove('is-loading');
        self.data = { nodes: d.nodes || [], edges: d.edges || [] };
        self._initLayout();
        self._applyYearFilter();
        self._renderMode();
      })
      .catch(function (e) {
        self.stage.classList.remove('is-loading');
        self.stage.innerHTML = '<div class="netviz-error">Yuklanmadi: ' + e.message + '</div>';
      });
  };

  // ---- layout / physics ----------------------------------------------------
  NetworkViz.prototype._initLayout = function () {
    var W = this.stage.clientWidth || 800, H = this.stage.clientHeight || 520;
    var i, n = this.data.nodes;
    this._index = {};
    for (i = 0; i < n.length; i++) {
      var node = n[i];
      node.x = W / 2 + (Math.random() - 0.5) * W * 0.8;
      node.y = H / 2 + (Math.random() - 0.5) * H * 0.8;
      node.vx = 0; node.vy = 0;
      node.r = (node.type === 'institution')
        ? Math.min(28, 10 + Math.sqrt(node.weight) * 3)
        : Math.min(16, 5 + Math.sqrt(node.weight) * 2);
      this._index[node.id] = node;
    }
    this.view = { k: 1, tx: 0, ty: 0 };
    this._alpha = 1;
    this._startSim();
  };

  NetworkViz.prototype._applyYearFilter = function () {
    var yf = this.yearFrom, yt = this.yearTo;
    this._visibleEdges = this.data.edges.filter(function (e) {
      return e.year == null || (e.year >= yf && e.year <= yt);
    });
  };

  NetworkViz.prototype._startSim = function () {
    var self = this;
    if (this._raf) cancelAnimationFrame(this._raf);
    var ticks = 0;
    function step() {
      if (self.mode === 'graph') { self._tick(); self._draw(); }
      ticks++;
      self._alpha *= 0.985;
      if (self._alpha > 0.02 && ticks < 600) self._raf = requestAnimationFrame(step);
    }
    step();
  };

  // Grid-bucketed repulsion keeps large node arrays performant (avoids O(n^2)).
  NetworkViz.prototype._tick = function () {
    var nodes = this.data.nodes, edges = this._visibleEdges || this.data.edges;
    var a = this._alpha, i, cell = 90, grid = {};
    for (i = 0; i < nodes.length; i++) {
      var gx = Math.floor(nodes[i].x / cell), gy = Math.floor(nodes[i].y / cell);
      (grid[gx + ',' + gy] || (grid[gx + ',' + gy] = [])).push(nodes[i]);
    }
    for (i = 0; i < nodes.length; i++) {
      var p = nodes[i], bx = Math.floor(p.x / cell), by = Math.floor(p.y / cell);
      for (var dx = -1; dx <= 1; dx++) for (var dy = -1; dy <= 1; dy++) {
        var bucket = grid[(bx + dx) + ',' + (by + dy)];
        if (!bucket) continue;
        for (var j = 0; j < bucket.length; j++) {
          var q = bucket[j]; if (q === p) continue;
          var ddx = p.x - q.x, ddy = p.y - q.y, d2 = ddx * ddx + ddy * ddy + 0.01;
          var f = 1400 * a / d2;
          p.vx += ddx * f; p.vy += ddy * f;
        }
      }
    }
    for (i = 0; i < edges.length; i++) {
      var s = this._index[edges[i].source], t = this._index[edges[i].target];
      if (!s || !t) continue;
      var ex = t.x - s.x, ey = t.y - s.y, dist = Math.sqrt(ex * ex + ey * ey) || 1;
      var k = (dist - 70) * 0.01 * a;
      var fx = ex / dist * k, fy = ey / dist * k;
      s.vx += fx; s.vy += fy; t.vx -= fx; t.vy -= fy;
    }
    var W = this.stage.clientWidth || 800, H = this.stage.clientHeight || 520;
    for (i = 0; i < nodes.length; i++) {
      var nn = nodes[i];
      nn.vx += (W / 2 - nn.x) * 0.0008 * a;
      nn.vy += (H / 2 - nn.y) * 0.0008 * a;
      if (nn._fixed) continue;
      nn.x += (nn.vx *= 0.85); nn.y += (nn.vy *= 0.85);
    }
  };

  // ---- rendering -----------------------------------------------------------
  NetworkViz.prototype._renderMode = function () {
    if (this.mode === 'map') { this._renderMap(); }
    else { this.canvas.style.display = ''; this._draw(); if (this._alpha < 0.02) { this._alpha = 0.4; this._startSim(); } }
  };

  NetworkViz.prototype._sizeCanvas = function () {
    var W = this.stage.clientWidth || 800, H = this.stage.clientHeight || 520;
    var dpr = global.devicePixelRatio || 1;
    this.canvas.width = W * dpr; this.canvas.height = H * dpr;
    this.canvas.style.width = W + 'px'; this.canvas.style.height = H + 'px';
    var ctx = this.canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  };

  NetworkViz.prototype._draw = function () {
    if (this.mode !== 'graph') return;
    var ctx = this._sizeCanvas();
    var W = this.stage.clientWidth || 800, H = this.stage.clientHeight || 520;
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(this.view.tx, this.view.ty); ctx.scale(this.view.k, this.view.k);

    var edges = this._visibleEdges || this.data.edges, i;
    ctx.lineWidth = 1;
    for (i = 0; i < edges.length; i++) {
      var s = this._index[edges[i].source], t = this._index[edges[i].target];
      if (!s || !t) continue;
      ctx.strokeStyle = EDGE_COLORS[edges[i].type] || '#94a3b8';
      ctx.globalAlpha = 0.55;
      ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(t.x, t.y); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    var nodes = this.data.nodes;
    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = (n.type === 'institution') ? '#0ea5e9' : '#6366f1';
      ctx.fill();
      if (n.type === 'institution' || n.r > 10) {
        ctx.fillStyle = '#0f172a'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText((n.label || '').slice(0, 22), n.x, n.y + n.r + 12);
      }
    }
    ctx.restore();
  };

  // ---- interaction: pan / zoom / drill-down --------------------------------
  NetworkViz.prototype._bindCanvas = function () {
    var self = this, drag = null;
    this.canvas.addEventListener('mousedown', function (ev) {
      drag = { x: ev.offsetX, y: ev.offsetY, tx: self.view.tx, ty: self.view.ty,
               moved: false };
    });
    global.addEventListener('mousemove', function (ev) {
      if (!drag || self.mode !== 'graph') return;
      var r = self.canvas.getBoundingClientRect();
      self.view.tx = drag.tx + (ev.clientX - r.left - drag.x);
      self.view.ty = drag.ty + (ev.clientY - r.top - drag.y);
      drag.moved = true; self._draw();
    });
    global.addEventListener('mouseup', function (ev) {
      if (drag && !drag.moved) self._onClick(ev);
      drag = null;
    });
    this.canvas.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      var f = ev.deltaY < 0 ? 1.1 : 0.9;
      self.view.k = Math.max(0.2, Math.min(4, self.view.k * f));
      self._draw();
    }, { passive: false });
  };

  NetworkViz.prototype._pick = function (ev) {
    var r = this.canvas.getBoundingClientRect();
    var mx = (ev.clientX - r.left - this.view.tx) / this.view.k;
    var my = (ev.clientY - r.top - this.view.ty) / this.view.k;
    var nodes = this.data.nodes;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i], dx = n.x - mx, dy = n.y - my;
      if (dx * dx + dy * dy <= n.r * n.r) return n;
    }
    return null;
  };

  // Clicking an institution executes a smooth focal zoom-to-fit on its subnetwork.
  NetworkViz.prototype._onClick = function (ev) {
    var n = this._pick(ev);
    if (!n || n.type !== 'institution') return;
    var members = [n], edges = this._visibleEdges || this.data.edges;
    for (var i = 0; i < edges.length; i++) {
      if (edges[i].source === n.id) { var t = this._index[edges[i].target]; if (t) members.push(t); }
    }
    this._zoomToFit(members);
  };

  NetworkViz.prototype._zoomToFit = function (nodes) {
    if (!nodes.length) return;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(function (n) {
      minX = Math.min(minX, n.x - n.r); minY = Math.min(minY, n.y - n.r);
      maxX = Math.max(maxX, n.x + n.r); maxY = Math.max(maxY, n.y + n.r);
    });
    var W = this.stage.clientWidth || 800, H = this.stage.clientHeight || 520;
    var pad = 60, k = Math.min(4, Math.min((W - pad) / (maxX - minX + 1),
                                           (H - pad) / (maxY - minY + 1)));
    var tk = { k: k, tx: W / 2 - k * (minX + maxX) / 2, ty: H / 2 - k * (minY + maxY) / 2 };
    this._animateView(tk);
  };

  NetworkViz.prototype._animateView = function (target) {
    var self = this, from = { k: this.view.k, tx: this.view.tx, ty: this.view.ty };
    var t0 = performance.now(), dur = 500;
    (function anim(now) {
      var p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      self.view.k = from.k + (target.k - from.k) * e;
      self.view.tx = from.tx + (target.tx - from.tx) * e;
      self.view.ty = from.ty + (target.ty - from.ty) * e;
      self._draw();
      if (p < 1) requestAnimationFrame(anim);
    })(t0);
  };

  // ---- Mode A: geographic map ---------------------------------------------
  NetworkViz.prototype._renderMap = function () {
    this.canvas.style.display = 'none';
    var host = this._mapHost || (this._mapHost = el('div', { class: 'netviz-map' }));
    host.style.width = '100%'; host.style.height = '100%';
    if (host.parentNode !== this.stage) this.stage.appendChild(host);

    if (!global.L || !global.L.map) {
      host.innerHTML = '<div class="netviz-error">Xarita rejimi uchun Leaflet ' +
        '(+ MarkerCluster) kutubxonasi sahifaga ulanishi kerak.</div>';
      return;
    }
    if (!this._map) {
      this._map = global.L.map(host).setView([41.31, 69.24], 6); // Toshkent
      global.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        { maxZoom: 18, attribution: '© OpenStreetMap' }).addTo(this._map);
    }
    if (this._cluster) this._map.removeLayer(this._cluster);
    // Performance-optimized clustering when the plugin is available.
    this._cluster = global.L.markerClusterGroup ? global.L.markerClusterGroup()
                                                 : global.L.layerGroup();
    var insts = this.data.nodes.filter(function (n) {
      return n.type === 'institution' && n.lat && n.lng;
    });
    for (var i = 0; i < insts.length; i++) {
      this._cluster.addLayer(global.L.marker([insts[i].lat, insts[i].lng])
        .bindPopup(insts[i].label));
    }
    this._map.addLayer(this._cluster);
    setTimeout(function () { if (this._map) this._map.invalidateSize(); }.bind(this), 50);
  };

  NetworkViz.mount = function (selector, opts) {
    var c = (typeof selector === 'string') ? document.querySelector(selector) : selector;
    if (!c) return null;
    return new NetworkViz(c, opts || {});
  };

  global.NetworkViz = NetworkViz;
})(window);
