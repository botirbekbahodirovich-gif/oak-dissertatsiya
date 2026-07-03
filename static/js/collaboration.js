/* ============================================================
 * CollabGraph — interactive academic collaboration network.
 * D3.js v7 force-directed graph. No maps, no external state.
 *
 * Data source (Flask):
 *   /api/collaboration?name=<scholar>   → ego network (BFS, ≤500 nodes)
 *   /api/collaboration?mode=full        → dense global graph
 *   /api/collaboration/search?q=<term>  → [{name, connections}]
 *
 * Edge types → colours:
 *   advisor      = 🟢 ustoz–shogird
 *   opponent     = 🔴 opponent
 *   institution  = 🔵 muassasa (same institution)
 *
 * Usage:  CollabGraph.mount({ svg, stage, search, ... })
 * ============================================================ */
(function (global) {
  'use strict';

  var EDGE_COLOR = { advisor: '#22c55e', opponent: '#ef4444', institution: '#3b82f6' };
  var ROLE_COLOR = { advisor: '#6366f1', mixed: '#8b5cf6', student: '#38bdf8', opponent: '#f97316' };
  var DEFAULT_Y0 = 2000, DEFAULT_Y1 = 2026;

  function $(sel) { return typeof sel === 'string' ? document.querySelector(sel) : sel; }
  function esc(s) { var x = document.createElement('div'); x.textContent = s == null ? '' : s; return x.innerHTML; }
  function short(s, n) { s = s || ''; return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function initials(name) {
    var p = (name || '').trim().split(/\s+/).filter(Boolean);
    if (!p.length) return '?';
    return (p[0][0] + (p.length > 1 ? p[1][0] : '')).toUpperCase();
  }
  function nodeRadius(d) {
    var base = 8 + Math.sqrt((d.students_count || d.students || 0) + 1) * 4;
    if (d.center) base = Math.max(base, 26);
    return Math.max(9, Math.min(38, base));
  }
  function debounce(fn, ms) {
    var t; return function () { var a = arguments, self = this; clearTimeout(t); t = setTimeout(function () { fn.apply(self, a); }, ms); };
  }

  function CollabGraph(cfg) {
    this.cfg = cfg;
    this.svgEl = $(cfg.svg);
    this.stageEl = $(cfg.stage);
    this.tipEl = cfg.tooltip ? $(cfg.tooltip) : null;
    this.infoEl = cfg.info ? $(cfg.info) : null;
    this.emptyEl = cfg.empty ? $(cfg.empty) : null;
    this.raw = { nodes: [], edges: [], center: '' };
    this.filters = { spec: '', y0: DEFAULT_Y0, y1: DEFAULT_Y1,
      types: { advisor: true, opponent: true, institution: false } };
    this.selected = null;
    this._uid = 0;
    this._wire();
    this.loadFull();
  }

  // ── wiring ──────────────────────────────────────────────
  CollabGraph.prototype._wire = function () {
    var self = this, c = this.cfg;

    // search + dropdown
    var q = $(c.search), drop = $(c.dropdown);
    this.qEl = q; this.dropEl = drop;
    if (q) {
      q.addEventListener('input', debounce(function () {
        var term = q.value.trim();
        if (term.length < 2) { self._closeDrop(); return; }
        fetch('/api/collaboration/search?q=' + encodeURIComponent(term))
          .then(function (r) { return r.json(); })
          .then(function (d) { self._showDrop(d.results || []); })
          .catch(function () { self._closeDrop(); });
      }, 220));
      q.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') { ev.preventDefault(); var first = drop && drop.querySelector('.cg-drop-item'); if (first) first.click(); }
        else if (ev.key === 'Escape') self._closeDrop();
      });
    }
    document.addEventListener('click', function (ev) {
      if (drop && !drop.contains(ev.target) && ev.target !== q) self._closeDrop();
    });

    // specialization filter
    var spec = $(c.spec); this.specEl = spec;
    if (spec) spec.addEventListener('input', debounce(function () {
      self.filters.spec = spec.value.trim(); self.render();
    }, 200));

    // connection-type checkboxes
    this.typeEls = {};
    ['advisor', 'opponent', 'institution'].forEach(function (t) {
      var el = c.types && c.types[t] ? $(c.types[t]) : null;
      if (!el) return;
      self.typeEls[t] = el;
      el.checked = self.filters.types[t];
      el.addEventListener('change', function () { self.filters.types[t] = el.checked; self.render(); });
    });

    // year range (two-handle)
    this.yFrom = $(c.yearFrom); this.yTo = $(c.yearTo);
    this.yOut = $(c.yearOut); this.yFill = $(c.yearFill);
    function onYear() {
      var a = +self.yFrom.value, b = +self.yTo.value;
      self.filters.y0 = Math.min(a, b); self.filters.y1 = Math.max(a, b);
      self._paintYear(); self.render();
    }
    if (this.yFrom) this.yFrom.addEventListener('input', onYear);
    if (this.yTo) this.yTo.addEventListener('input', onYear);

    // resize redraw
    global.addEventListener('resize', debounce(function () { self.render(); }, 250));
  };

  CollabGraph.prototype._showDrop = function (results) {
    var self = this, drop = this.dropEl;
    if (!drop) return;
    if (!results.length) { drop.innerHTML = '<div class="cg-drop-empty">Hech narsa topilmadi</div>'; drop.classList.add('open'); return; }
    drop.innerHTML = results.map(function (r) {
      return '<div class="cg-drop-item" data-n="' + esc(r.name) + '">' +
        '<span class="n">' + esc(r.name) + '</span>' +
        '<span class="c">' + (r.connections || 0) + ' aloqa</span></div>';
    }).join('');
    Array.prototype.forEach.call(drop.querySelectorAll('.cg-drop-item'), function (it) {
      it.addEventListener('click', function () {
        var name = it.getAttribute('data-n');
        if (self.qEl) self.qEl.value = name;
        self._closeDrop();
        self.loadCenter(name);
      });
    });
    drop.classList.add('open');
  };
  CollabGraph.prototype._closeDrop = function () { if (this.dropEl) this.dropEl.classList.remove('open'); };

  CollabGraph.prototype._paintYear = function () {
    if (!this.yFrom || !this.yTo) return;
    var min = +this.yFrom.min, max = +this.yFrom.max, span = (max - min) || 1;
    var a = Math.min(+this.yFrom.value, +this.yTo.value), b = Math.max(+this.yFrom.value, +this.yTo.value);
    if (this.yFill) {
      this.yFill.style.left = ((a - min) / span * 100) + '%';
      this.yFill.style.width = ((b - a) / span * 100) + '%';
    }
    if (this.yOut) this.yOut.textContent = a + ' – ' + b;
  };

  // ── data loading ────────────────────────────────────────
  CollabGraph.prototype._loading = function (on) {
    if (this.stageEl) this.stageEl.classList.toggle('is-loading', !!on);
  };

  CollabGraph.prototype.loadFull = function () {
    var self = this;
    this._loading(true);
    fetch('/api/collaboration?mode=full')
      .then(function (r) { return r.json(); })
      .then(function (d) { self._loading(false); self.setData(d); })
      .catch(function () { self._loading(false); self._empty(true); });
  };

  CollabGraph.prototype.loadCenter = function (name) {
    var self = this;
    if (!name) return;
    this._loading(true);
    fetch('/api/collaboration?name=' + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        self._loading(false);
        if (!d.nodes || !d.nodes.length) { self._empty(true); return; }
        d.center = d.center || name;
        self.setData(d);
      })
      .catch(function () { self._loading(false); self._empty(true); });
  };

  CollabGraph.prototype.setData = function (d) {
    this.raw = { nodes: d.nodes || [], edges: d.edges || [], center: d.center || '' };
    // derive year bounds from edges
    var years = this.raw.edges.map(function (e) { return e.year; }).filter(function (y) { return y != null; });
    var min = years.length ? Math.min.apply(null, years) : DEFAULT_Y0;
    var max = years.length ? Math.max.apply(null, years) : DEFAULT_Y1;
    if (min === max) { min -= 1; max += 1; }
    if (this.yFrom && this.yTo) {
      this.yFrom.min = this.yTo.min = min; this.yFrom.max = this.yTo.max = max;
      this.yFrom.value = min; this.yTo.value = max;
      this.filters.y0 = min; this.filters.y1 = max;
      this._paintYear();
    }
    this.selected = null;
    this._closeInfo();
    this.render();
  };

  CollabGraph.prototype._empty = function (on) {
    if (this.emptyEl) this.emptyEl.style.display = on ? 'flex' : 'none';
    if (on && this.svgEl) d3.select(this.svgEl).selectAll('*').remove();
  };

  // ── filtering ───────────────────────────────────────────
  CollabGraph.prototype._filtered = function () {
    var f = this.filters, center = this.raw.center;
    var specOn = !!f.spec;
    var nodeById = {};
    this.raw.nodes.forEach(function (n) { nodeById[n.id] = n; });

    // node passes specialization filter (center always kept)
    function specOk(n) {
      if (!specOn) return true;
      if (n.id === center) return true;
      return (n.specialization || '').indexOf(f.spec) === 0 ||
             (n.specialization || '').indexOf(f.spec) > -1;
    }

    var edges = this.raw.edges.filter(function (e) {
      if (!f.types[e.type]) return false;
      if (e.year != null && (e.year < f.y0 || e.year > f.y1)) return false;
      var s = nodeById[e.source], t = nodeById[e.target];
      if (!s || !t) return false;
      return specOk(s) && specOk(t);
    });

    // keep nodes that still carry a visible edge (or are the centre / match spec directly)
    var keep = {};
    edges.forEach(function (e) { keep[e.source] = true; keep[e.target] = true; });
    if (center) keep[center] = true;
    var nodes = this.raw.nodes.filter(function (n) {
      if (!specOk(n)) return false;
      if (keep[n.id]) return true;
      return specOn && n.id !== center ? true : false; // when filtering by spec, show matched even if isolated
    });
    // drop edges whose endpoints were pruned
    var present = {}; nodes.forEach(function (n) { present[n.id] = true; });
    edges = edges.filter(function (e) { return present[e.source] && present[e.target]; });
    return { nodes: nodes, edges: edges };
  };

  // ── render ──────────────────────────────────────────────
  CollabGraph.prototype.render = function () {
    var self = this;
    if (!this.svgEl) return;
    var data = this._filtered();
    if (!data.nodes.length) { this._empty(true); d3.select(this.svgEl).selectAll('*').remove(); return; }
    this._empty(false);

    var svg = d3.select(this.svgEl);
    svg.selectAll('*').remove();
    var el = this.svgEl;
    var width = el.clientWidth || 900, height = el.clientHeight || 560;
    var center = this.raw.center;

    // clone so the simulation can mutate coordinates
    var nodes = data.nodes.map(function (n) { return Object.assign({}, n); });
    var byId = {}; nodes.forEach(function (n) { byId[n.id] = n; });
    var links = data.edges.map(function (e) { return { source: e.source, target: e.target, type: e.type, weight: e.weight || 1, year: e.year }; });

    var defs = svg.append('defs');
    var glow = defs.append('filter').attr('id', 'cg-glow').attr('x', '-60%').attr('y', '-60%').attr('width', '220%').attr('height', '220%');
    glow.append('feDropShadow').attr('dx', 0).attr('dy', 0).attr('stdDeviation', 6).attr('flood-color', '#60a5fa').attr('flood-opacity', 0.95);

    var g = svg.append('g');
    var zoom = d3.zoom().scaleExtent([0.15, 5]).on('zoom', function (ev) { g.attr('transform', ev.transform); });
    svg.call(zoom).on('dblclick.zoom', null);
    this._zoom = zoom;

    // links
    var linkSel = g.append('g').attr('stroke-linecap', 'round').selectAll('line')
      .data(links).enter().append('line')
      .attr('stroke', function (d) { return EDGE_COLOR[d.type] || '#64748b'; })
      .attr('stroke-opacity', function (d) { return d.type === 'institution' ? 0.28 : 0.5; })
      .attr('stroke-width', function (d) { return Math.max(1, Math.min(4, d.weight * 1.2)); })
      .attr('stroke-dasharray', function (d) { return d.type === 'opponent' ? '5 4' : d.type === 'institution' ? '2 5' : null; });

    // nodes
    var nodeSel = g.append('g').selectAll('g.cnode')
      .data(nodes).enter().append('g').attr('class', 'cnode').style('cursor', 'pointer');

    nodeSel.each(function (d) {
      d._uid = 'cgn' + (self._uid++);
      var r = nodeRadius(d);
      var grp = d3.select(this);
      // clip for avatar
      defs.append('clipPath').attr('id', d._uid + 'clip').append('circle').attr('r', r - 2);
      // base circle (role colour)
      grp.append('circle')
        .attr('r', r)
        .attr('fill', ROLE_COLOR[d.role] || '#3b82f6')
        .attr('stroke', d.id === center ? '#fff' : 'rgba(255,255,255,0.28)')
        .attr('stroke-width', d.id === center ? 3 : 1.5)
        .style('filter', d.id === center ? 'url(#cg-glow)' : null);
      // initials (revealed if the avatar fails / is missing)
      grp.append('text').attr('class', 'cg-ini')
        .text(initials(d.id)).attr('text-anchor', 'middle').attr('dy', '0.34em')
        .attr('fill', '#fff').attr('font-weight', 700)
        .attr('font-size', Math.max(9, r * 0.7)).style('pointer-events', 'none');
      // avatar image on top (hidden until it loads successfully)
      if (d.avatar) {
        var img = grp.append('image')
          .attr('href', d.avatar).attr('xlink:href', d.avatar)
          .attr('x', -(r - 2)).attr('y', -(r - 2))
          .attr('width', (r - 2) * 2).attr('height', (r - 2) * 2)
          .attr('clip-path', 'url(#' + d._uid + 'clip)')
          .attr('preserveAspectRatio', 'xMidYMid slice')
          .style('opacity', 0).style('pointer-events', 'none');
        img.node().addEventListener('load', function () { img.style('opacity', 1); });
        img.node().addEventListener('error', function () { img.remove(); });
      }
      // centre gets a pulsing ring
      if (d.id === center) {
        grp.select('circle').append('animate').attr('attributeName', 'stroke-opacity')
          .attr('values', '1;0.35;1').attr('dur', '1.8s').attr('repeatCount', 'indefinite');
      }
    });

    // label below node
    nodeSel.append('text')
      .text(function (d) { return short(d.id, 16); })
      .attr('text-anchor', 'middle')
      .attr('dy', function (d) { return nodeRadius(d) + 12; })
      .attr('fill', '#cbd5e1').attr('font-size', function (d) { return Math.max(9, Math.min(13, nodeRadius(d) * 0.55)); })
      .style('pointer-events', 'none').style('paint-order', 'stroke')
      .style('stroke', '#0b1220').style('stroke-width', '3px');

    // simulation
    var sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(function (d) {
        return d.type === 'advisor' ? 78 : d.type === 'opponent' ? 120 : 95;
      }).strength(function (d) { return d.type === 'institution' ? 0.15 : 0.6; }))
      .force('charge', d3.forceManyBody().strength(nodes.length > 200 ? -120 : -240))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(function (d) { return nodeRadius(d) + 6; }))
      .on('tick', ticked);
    this._sim = sim;

    function ticked() {
      linkSel.attr('x1', function (d) { return d.source.x; }).attr('y1', function (d) { return d.source.y; })
        .attr('x2', function (d) { return d.target.x; }).attr('y2', function (d) { return d.target.y; });
      nodeSel.attr('transform', function (d) { return 'translate(' + d.x + ',' + d.y + ')'; });
    }

    // drag
    nodeSel.call(d3.drag()
      .on('start', function (ev, d) { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', function (ev, d) { d.fx = ev.x; d.fy = ev.y; })
      .on('end', function (ev, d) { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

    // highlight neighbourhood
    function neighborsOf(id) {
      var set = {}; set[id] = true;
      links.forEach(function (l) {
        var s = l.source.id || l.source, t = l.target.id || l.target;
        if (s === id) set[t] = true; if (t === id) set[s] = true;
      });
      return set;
    }
    function highlight(id) {
      var nb = neighborsOf(id);
      nodeSel.style('opacity', function (d) { return nb[d.id] ? 1 : 0.14; });
      linkSel.style('opacity', function (l) {
        var s = l.source.id || l.source, t = l.target.id || l.target;
        return (s === id || t === id) ? 1 : 0.05;
      });
    }
    function clearHighlight() { nodeSel.style('opacity', 1); linkSel.style('opacity', null); }
    this._clearHighlight = clearHighlight;

    // tooltip
    var tip = this.tipEl ? d3.select(this.tipEl) : null;
    nodeSel.on('mouseenter', function (ev, d) {
      d3.select(this).select('circle').transition().duration(120).attr('transform', 'scale(1.25)');
      if (tip) {
        var rows = [];
        if (d.students_count || d.students) rows.push((d.students_count || d.students) + ' shogird');
        if (d.opponents) rows.push(d.opponents + ' opponentlik');
        if (d.specialization) rows.push('ixtisoslik ' + esc(d.specialization));
        tip.html('<b>' + esc(d.id) + '</b>' + (d.degree ? ' · ' + esc(d.degree) : '') +
          '<br><span class="muted">' + (d.connections || 0) + ' ta aloqa</span>' +
          (rows.length ? '<br>' + rows.join(' · ') : '') +
          (d.institution ? '<br><span class="muted">' + esc(short(d.institution, 40)) + '</span>' : ''))
          .style('opacity', 1);
        move(ev);
      }
    }).on('mousemove', move).on('mouseleave', function () {
      d3.select(this).select('circle').transition().duration(120).attr('transform', 'scale(1)');
      if (tip) tip.style('opacity', 0);
    });
    function move(ev) {
      if (!tip) return;
      var r = self.stageEl.getBoundingClientRect();
      var x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
      var tw = self.tipEl.offsetWidth || 200;
      if (x + tw > r.width) x = ev.clientX - r.left - tw - 14;
      tip.style('left', x + 'px').style('top', y + 'px');
    }

    // click → highlight + info panel ; double-click → recenter (lazy load)
    nodeSel.on('click', function (ev, d) {
      ev.stopPropagation();
      if (self.selected === d.id) { self.selected = null; clearHighlight(); self._closeInfo(); return; }
      self.selected = d.id; highlight(d.id); self._showInfo(d);
    });
    nodeSel.on('dblclick', function (ev, d) { ev.stopPropagation(); self.loadCenter(d.id); });
    svg.on('click', function () { self.selected = null; clearHighlight(); self._closeInfo(); });

    // fit to view once settled
    this._fit(svg, zoom, nodes, width, height);
  };

  CollabGraph.prototype._fit = function (svg, zoom, nodes, width, height) {
    setTimeout(function () {
      if (!nodes.length) return;
      var minX = d3.min(nodes, function (n) { return n.x; }), maxX = d3.max(nodes, function (n) { return n.x; });
      var minY = d3.min(nodes, function (n) { return n.y; }), maxY = d3.max(nodes, function (n) { return n.y; });
      if (minX == null) return;
      var bw = (maxX - minX) || 1, bh = (maxY - minY) || 1;
      var scale = Math.min(width / (bw + 140), height / (bh + 140), 1.4);
      var tx = width / 2 - ((minX + maxX) / 2) * scale, ty = height / 2 - ((minY + maxY) / 2) * scale;
      svg.transition().duration(650).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    }, 850);
  };

  // ── info panel ──────────────────────────────────────────
  CollabGraph.prototype._showInfo = function (d) {
    var panel = this.infoEl; if (!panel) return;
    var deg = d.degree ? '<span class="ip-deg ' + (d.degree === 'DSc' ? 'dsc' : 'phd') + '">' + esc(d.degree) + '</span>' : '';
    var meta = [];
    if (d.specialization) meta.push('Ixtisoslik: <b>' + esc(d.specialization) + '</b>');
    if (d.institution) meta.push(esc(short(d.institution, 46)));
    var stats = [];
    stats.push('<b>' + (d.students_count || d.students || 0) + '</b> shogird');
    stats.push('<b>' + (d.advisors || 0) + '</b> rahbar');
    stats.push('<b>' + (d.opponents || 0) + '</b> opponentlik');
    var enc = encodeURIComponent(d.id);
    panel.innerHTML =
      '<button class="ip-close" aria-label="Yopish">✕</button>' +
      '<h3>' + esc(d.id) + ' ' + deg + '</h3>' +
      (meta.length ? '<div class="ip-meta">' + meta.join('<br>') + '</div>' : '') +
      '<div class="ip-stats">' + stats.join(' · ') + '</div>' +
      '<div class="ip-actions">' +
        '<a class="ip-btn cg-recenter">🔄 Shu olimni markazga qo\'yish</a>' +
        '<a class="ip-btn ghost" href="/olim/' + enc + '">👤 Profilni ko\'rish →</a>' +
        '<a class="ip-btn ghost" href="/genealogy/' + enc + '">🌳 Shajarani ko\'rish →</a>' +
      '</div>';
    var self = this;
    panel.querySelector('.ip-close').addEventListener('click', function () { self._closeInfo(); self.selected = null; if (self._clearHighlight) self._clearHighlight(); });
    panel.querySelector('.cg-recenter').addEventListener('click', function () { self.loadCenter(d.id); });
    panel.classList.add('open');
  };
  CollabGraph.prototype._closeInfo = function () { if (this.infoEl) this.infoEl.classList.remove('open'); };

  // ── public API ──────────────────────────────────────────
  var API = {
    mount: function (cfg) {
      if (!cfg || !$(cfg.svg)) return null;
      return new CollabGraph(cfg);
    }
  };
  global.CollabGraph = API;
})(window);
