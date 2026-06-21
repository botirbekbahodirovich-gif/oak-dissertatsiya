/* Collaboration network — D3.js v7 force-directed graph. window.renderCollab(opts) */
(function () {
  'use strict';

  var ROLE_COLOR = { advisor: '#1e40af', mixed: '#3b82f6', student: '#059669', opponent: '#e36403' };
  function nodeColor(d) { return ROLE_COLOR[d.role] || '#3b82f6'; }
  function nodeRadius(d) { return Math.max(8, Math.min(30, Math.sqrt(d.connections || 1) * 4)); }
  function esc(s) { var x = document.createElement('div'); x.textContent = s || ''; return x.innerHTML; }
  function short(s, n) { s = s || ''; return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  window.renderCollab = function (opts) {
    var svg = d3.select(opts.svg);
    var data = opts.data || { nodes: [], edges: [] };
    var tip = opts.tooltip ? d3.select(opts.tooltip) : null;
    var info = opts.info || null;          // info panel element id
    var onRecenter = opts.onRecenter || function () {};
    var centerName = (data.center || opts.centerName || '');

    svg.selectAll('*').remove();
    var el = svg.node();
    var width = el.clientWidth || 900, height = el.clientHeight || 560;

    // clone so simulation can mutate
    var nodes = data.nodes.map(function (n) { return Object.assign({}, n); });
    var byId = {}; nodes.forEach(function (n) { byId[n.id] = n; });
    var links = (data.edges || []).filter(function (e) {
      return byId[e.source] && byId[e.target];
    }).map(function (e) { return { source: e.source, target: e.target, type: e.type, weight: e.weight || 1 }; });

    var defs = svg.append('defs');
    var glow = defs.append('filter').attr('id', 'collab-glow').attr('x', '-60%').attr('y', '-60%').attr('width', '220%').attr('height', '220%');
    glow.append('feDropShadow').attr('dx', 0).attr('dy', 0).attr('stdDeviation', 6).attr('flood-color', '#60a5fa').attr('flood-opacity', 0.95);

    var g = svg.append('g');
    var zoom = d3.zoom().scaleExtent([0.2, 4]).on('zoom', function (ev) { g.attr('transform', ev.transform); });
    svg.call(zoom);

    var linkSel = g.append('g').attr('stroke-linecap', 'round').selectAll('line')
      .data(links).enter().append('line')
      .attr('stroke', function (d) { return d.type === 'opponent' ? '#e36403' : d.type === 'sibling' ? '#059669' : '#3b82f6'; })
      .attr('stroke-opacity', function (d) { return d.type === 'opponent' ? 0.3 : d.type === 'sibling' ? 0.22 : 0.4; })
      .attr('stroke-width', function (d) { return Math.max(1, Math.min(3, d.weight)); })
      .attr('stroke-dasharray', function (d) { return d.type === 'opponent' ? '5 4' : d.type === 'sibling' ? '2 4' : null; });

    var nodeSel = g.append('g').selectAll('g.cnode')
      .data(nodes).enter().append('g').attr('class', 'cnode').style('cursor', 'pointer');

    nodeSel.append('circle')
      .attr('r', nodeRadius)
      .attr('fill', nodeColor)
      .attr('stroke', function (d) { return (d.center || d.id === centerName) ? '#fff' : 'rgba(255,255,255,0.25)'; })
      .attr('stroke-width', function (d) { return (d.center || d.id === centerName) ? 3 : 1.5; })
      .style('filter', function (d) { return (d.center || d.id === centerName) ? 'url(#collab-glow)' : null; });

    nodeSel.append('text')
      .text(function (d) { return short(d.id, 15); })
      .attr('text-anchor', 'middle')
      .attr('dy', function (d) { return nodeRadius(d) + 11; })
      .attr('fill', '#cbd5e1')
      .attr('font-size', function (d) { return Math.max(9, Math.min(13, nodeRadius(d) * 0.6)); })
      .style('pointer-events', 'none');

    // pulse animation for center
    nodeSel.filter(function (d) { return d.center || d.id === centerName; })
      .select('circle')
      .append('animate').attr('attributeName', 'stroke-opacity').attr('values', '1;0.3;1').attr('dur', '1.8s').attr('repeatCount', 'indefinite');

    var sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(function (d) {
        return d.type === 'advisor' ? 80 : d.type === 'opponent' ? 120 : 100;
      }))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(function (d) { return nodeRadius(d) + 5; }))
      .on('tick', ticked);

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
      nodeSel.style('opacity', function (d) { return nb[d.id] ? 1 : 0.15; });
      linkSel.style('opacity', function (l) {
        var s = l.source.id || l.source, t = l.target.id || l.target;
        return (s === id || t === id) ? 1 : 0.05;
      });
    }
    function clearHighlight() { nodeSel.style('opacity', 1); linkSel.style('opacity', null); }

    // hover tooltip
    nodeSel.on('mouseenter', function (ev, d) {
      d3.select(this).select('circle').transition().duration(120).attr('transform', 'scale(1.3)');
      if (tip) {
        var roles = [];
        if (d.students) roles.push(d.students + ' shogird');
        if (d.advisors) roles.push(d.advisors + ' rahbar');
        if (d.opponents) roles.push(d.opponents + ' opponent');
        tip.html('<b>' + esc(d.id) + '</b>' + (d.degree ? ' · ' + d.degree : '') +
          '<br>' + d.connections + ' ta aloqa' + (roles.length ? '<br>' + roles.join(' · ') : ''))
          .style('opacity', 1).style('left', (ev.offsetX + 14) + 'px').style('top', (ev.offsetY + 14) + 'px');
      }
    }).on('mousemove', function (ev) {
      if (tip) tip.style('left', (ev.offsetX + 14) + 'px').style('top', (ev.offsetY + 14) + 'px');
    }).on('mouseleave', function () {
      d3.select(this).select('circle').transition().duration(120).attr('transform', 'scale(1)');
      if (tip) tip.style('opacity', 0);
    });

    var selected = null;
    nodeSel.on('click', function (ev, d) {
      ev.stopPropagation();
      if (selected === d.id) { selected = null; clearHighlight(); if (info) document.getElementById(info).classList.remove('open'); return; }
      selected = d.id; highlight(d.id);
      if (info) showInfo(d);
    });
    svg.on('click', function () { selected = null; clearHighlight(); if (info) document.getElementById(info).classList.remove('open'); });

    nodeSel.on('dblclick', function (ev, d) { ev.stopPropagation(); window.location.href = '/olim/' + encodeURIComponent(d.id); });
    nodeSel.on('contextmenu', function (ev, d) { ev.preventDefault(); onRecenter(d.id); });

    function showInfo(d) {
      var panel = document.getElementById(info);
      if (!panel) return;
      // top connections by weight
      var w = {};
      links.forEach(function (l) {
        var s = l.source.id || l.source, t = l.target.id || l.target;
        if (s === d.id) w[t] = (w[t] || 0) + (l.weight || 1);
        else if (t === d.id) w[s] = (w[s] || 0) + (l.weight || 1);
      });
      var top = Object.keys(w).sort(function (a, b) { return w[b] - w[a]; }).slice(0, 5);
      var deg = d.degree ? '<span class="ip-deg ' + (d.degree === 'DSc' ? 'dsc' : 'phd') + '">' + d.degree + '</span>' : '';
      var stats = [];
      stats.push('<b>' + d.students + '</b> shogird');
      stats.push('<b>' + d.advisors + '</b> rahbar');
      stats.push('<b>' + d.opponents + '</b> opponent');
      panel.innerHTML =
        '<button class="ip-close" onclick="document.getElementById(\'' + info + '\').classList.remove(\'open\')">✕</button>' +
        '<h3>' + esc(d.id) + ' ' + deg + '</h3>' +
        '<div class="ip-stats">' + stats.join(' · ') + '</div>' +
        (top.length ? '<div class="ip-sub">Asosiy aloqalar</div><div class="ip-conn">' +
          top.map(function (n) { return '<a href="/olim/' + encodeURIComponent(n) + '">' + esc(short(n, 30)) + '</a>'; }).join('') + '</div>' : '') +
        '<div class="ip-actions">' +
        '<a class="ip-btn" href="/olim/' + encodeURIComponent(d.id) + '">Profilni ko\'rish →</a>' +
        '<a class="ip-btn ghost" href="/genealogy/' + encodeURIComponent(d.id) + '">Shajarani ko\'rish →</a>' +
        '</div>';
      panel.classList.add('open');
    }

    function fit() {
      setTimeout(function () {
        var nl = nodes; if (!nl.length) return;
        var minX = d3.min(nl, function (n) { return n.x; }), maxX = d3.max(nl, function (n) { return n.x; });
        var minY = d3.min(nl, function (n) { return n.y; }), maxY = d3.max(nl, function (n) { return n.y; });
        var bw = (maxX - minX) || 1, bh = (maxY - minY) || 1;
        var scale = Math.min(width / (bw + 120), height / (bh + 120), 1.2);
        var tx = width / 2 - ((minX + maxX) / 2) * scale, ty = height / 2 - ((minY + maxY) / 2) * scale;
        svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
      }, 900);
    }
    fit();

    return { reset: fit, stop: function () { sim.stop(); } };
  };
})();
