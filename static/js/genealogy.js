/* Academic genealogy tree renderer (D3.js v7). Public API: window.renderGenealogy(opts) */
(function () {
  'use strict';

  var DEG = {
    PhD: { bg: '#059669', label: 'PhD' },
    DSc: { bg: '#e36403', label: 'DSc' }
  };

  function degColor(d) { return DEG[d] ? DEG[d].bg : '#334155'; }

  function sizeFor(role) {
    if (role === 'center') return { w: 200, h: 70 };
    if (role === 'sibling') return { w: 146, h: 52 };
    return { w: 170, h: 58 };
  }

  // tier offsets (y multiplier)
  var TIER = { grandparent: -2, parent: -1, center: 0, sibling: 0, child: 1, grandchild: 2 };
  var TIER_H = 140;
  var GAP = 26;

  window.renderGenealogy = function (opts) {
    var svgSel = opts.svg;
    var data = opts.data || {};
    var tooltipSel = opts.tooltip;
    var center = (data.center && data.center.name) || opts.centerName || '';

    var svg = d3.select(svgSel);
    svg.selectAll('*').remove();
    var tip = tooltipSel ? d3.select(tooltipSel) : null;

    // ── build graph ──
    var nodes = {};      // name -> node
    var links = [];      // {source, target}
    function addNode(name, role, degree, extra) {
      if (!name) return null;
      if (nodes[name]) {
        // upgrade role priority: center > parent/child > sibling/grand
        return nodes[name];
      }
      var sz = sizeFor(role);
      nodes[name] = {
        name: name, role: role, degree: degree || null,
        tier: TIER[role], w: sz.w, h: sz.h,
        cc: (extra && extra.children_count) || 0,
        dc: (extra && extra.dissertation_count) || 0,
        expanded: false
      };
      return nodes[name];
    }
    function addLink(a, b) { if (a && b) links.push({ s: a, t: b }); }

    var cNode = addNode(center, 'center', data.center && data.center.degree,
      { dissertation_count: data.center && data.center.dissertation_count });

    (data.parents || []).forEach(function (p) {
      var pn = addNode(p.name, 'parent', p.degree);
      addLink(pn, cNode);
      (p.parents || []).forEach(function (g) {
        var gn = addNode(g.name, 'grandparent', g.degree);
        addLink(gn, pn);
      });
    });
    (data.children || []).forEach(function (c) {
      var cn = addNode(c.name, 'child', c.degree, { children_count: c.children_count });
      addLink(cNode, cn);
    });
    var firstParent = (data.parents && data.parents[0]) ? nodes[data.parents[0].name] : null;
    (data.siblings || []).forEach(function (s) {
      var sn = addNode(s.name, 'sibling', s.degree);
      if (firstParent) addLink(firstParent, sn);
    });

    // ── zoom container ──
    var g = svg.append('g');
    var zoom = d3.zoom().scaleExtent([0.3, 2.5]).on('zoom', function (ev) {
      g.attr('transform', ev.transform);
    });
    svg.call(zoom);

    var linkLayer = g.append('g').attr('class', 'gen-links');
    var nodeLayer = g.append('g').attr('class', 'gen-nodes');

    // glow filter for the center node
    var defs = svg.append('defs');
    var f = defs.append('filter').attr('id', 'gen-glow').attr('x', '-50%').attr('y', '-50%')
      .attr('width', '200%').attr('height', '200%');
    f.append('feDropShadow').attr('dx', 0).attr('dy', 0).attr('stdDeviation', 6)
      .attr('flood-color', '#3b82f6').attr('flood-opacity', 0.9);

    function layout() {
      // group by tier
      var tiers = {};
      Object.keys(nodes).forEach(function (k) {
        var n = nodes[k];
        (tiers[n.tier] = tiers[n.tier] || []).push(n);
      });
      Object.keys(tiers).forEach(function (t) {
        var row = tiers[t];
        var y = (+t) * TIER_H;
        if (+t === 0) {
          // center stays at x=0, siblings spread around it
          var sibs = row.filter(function (n) { return n.role !== 'center'; });
          cNode.x = 0; cNode.y = y;
          var span = 200 + GAP + 146;
          sibs.forEach(function (n, i) {
            var side = (i % 2 === 0) ? 1 : -1;
            var step = Math.floor(i / 2) + 1;
            n.x = side * (span / 2 + (step - 1) * (146 + GAP));
            n.y = y;
          });
        } else {
          var totalW = row.reduce(function (a, n) { return a + n.w + GAP; }, -GAP);
          var startX = -totalW / 2;
          var cx = startX;
          row.forEach(function (n) { n.x = cx + n.w / 2; n.y = y; cx += n.w + GAP; });
        }
      });
    }

    function topLink(d) {
      return { source: { x: d.s.x, y: d.s.y + d.s.h / 2 }, target: { x: d.t.x, y: d.t.y - d.t.h / 2 } };
    }
    var linkGen = d3.linkVertical().x(function (p) { return p.x; }).y(function (p) { return p.y; });

    function draw() {
      layout();
      // links
      var ls = linkLayer.selectAll('path').data(links, function (d) { return d.s.name + '>' + d.t.name; });
      ls.exit().remove();
      ls.enter().append('path')
        .attr('class', 'gen-link')
        .attr('fill', 'none').attr('stroke', '#cbd5e1').attr('stroke-width', 2).attr('stroke-opacity', 0.5)
        .merge(ls)
        .attr('d', function (d) { return linkGen(topLink(d)); })
        .attr('data-s', function (d) { return d.s.name; })
        .attr('data-t', function (d) { return d.t.name; });

      // nodes
      var ns = nodeLayer.selectAll('g.gen-node').data(Object.values(nodes), function (d) { return d.name; });
      ns.exit().remove();
      var enter = ns.enter().append('g')
        .attr('class', 'gen-node')
        .style('cursor', 'pointer')
        .style('opacity', 0)
        .attr('transform', function (d) { return 'translate(' + d.x + ',' + d.y + ') scale(0.6)'; });

      enter.each(function (d) {
        var grp = d3.select(this);
        var rad = 10;
        grp.append('rect')
          .attr('class', 'gn-rect')
          .attr('x', -d.w / 2).attr('y', -d.h / 2).attr('width', d.w).attr('height', d.h)
          .attr('rx', rad).attr('ry', rad);
        // avatar circle
        grp.append('circle').attr('class', 'gn-av')
          .attr('cx', -d.w / 2 + 20).attr('cy', 0).attr('r', 13);
        grp.append('text').attr('class', 'gn-emoji')
          .attr('x', -d.w / 2 + 20).attr('y', 5).attr('text-anchor', 'middle').text('🎓');
        // name
        grp.append('text').attr('class', 'gn-name')
          .attr('x', -d.w / 2 + 40).attr('y', d.degree ? -2 : 4)
          .text(shorten(d.name, d.role === 'center' ? 24 : 18));
        // degree badge
        if (d.degree) {
          grp.append('rect').attr('class', 'gn-badge')
            .attr('x', -d.w / 2 + 40).attr('y', 8).attr('width', 38).attr('height', 16).attr('rx', 8)
            .attr('fill', degColor(d.degree));
          grp.append('text').attr('class', 'gn-badge-t')
            .attr('x', -d.w / 2 + 59).attr('y', 20).attr('text-anchor', 'middle').text(d.degree);
        }
        // expand "+" for parents and children
        if (d.role === 'parent' || d.role === 'child') {
          var plus = grp.append('g').attr('class', 'gn-plus')
            .attr('transform', 'translate(' + (d.w / 2 - 14) + ',0)');
          plus.append('circle').attr('r', 10);
          plus.append('text').attr('y', 4).attr('text-anchor', 'middle').text('+');
          plus.on('click', function (ev) { ev.stopPropagation(); expand(d); });
        }
      });

      var all = enter.merge(ns);
      all.transition().duration(450)
        .style('opacity', 1)
        .attr('transform', function (d) { return 'translate(' + d.x + ',' + d.y + ') scale(1)'; });

      // style rects by role/degree
      all.select('rect.gn-rect')
        .attr('fill', function (d) {
          if (d.role === 'center') return '#1e40af';
          if (d.role === 'parent' || d.role === 'grandparent') return '#1e3a8a';
          if (d.role === 'sibling') return '#334155';
          return '#162032';
        })
        .attr('stroke', function (d) {
          if (d.role === 'center') return '#3b82f6';
          if (d.role === 'child' || d.role === 'grandchild') return degColor(d.degree);
          return '#475569';
        })
        .attr('stroke-width', function (d) { return d.role === 'center' ? 3 : 2; })
        .style('filter', function (d) { return d.role === 'center' ? 'url(#gen-glow)' : null; });

      all.select('circle.gn-av').attr('fill', function (d) {
        return d.role === 'child' ? degColor(d.degree) : '#3b82f6';
      });

      // interactions
      all.on('click', function (ev, d) {
        if (d.name === center) return;
        window.location.href = '/olim/' + encodeURIComponent(d.name);
      });
      all.on('mouseenter', function (ev, d) {
        linkLayer.selectAll('path').attr('stroke-opacity', function (l) {
          return (l.s.name === d.name || l.t.name === d.name) ? 1 : 0.15;
        }).attr('stroke', function (l) {
          return (l.s.name === d.name || l.t.name === d.name) ? '#60a5fa' : '#cbd5e1';
        });
        if (tip) {
          var bits = [];
          if (d.degree) bits.push(d.degree);
          if (d.role === 'child' && d.cc) bits.push(d.cc + ' ta shogird');
          if (d.role === 'center' && d.dc) bits.push(d.dc + ' ta himoya');
          var roleLbl = { center: 'Markaziy', parent: 'Ilmiy rahbar', grandparent: 'Rahbarning rahbari',
            child: 'Shogird', grandchild: 'Shogirdning shogirdi', sibling: 'Hamkasb' }[d.role] || '';
          tip.html('<b>' + escapeHtml(d.name) + '</b><br>' + roleLbl + (bits.length ? ' · ' + bits.join(' · ') : ''))
            .style('opacity', 1)
            .style('left', (ev.offsetX + 14) + 'px').style('top', (ev.offsetY + 14) + 'px');
        }
      });
      all.on('mousemove', function (ev) {
        if (tip) tip.style('left', (ev.offsetX + 14) + 'px').style('top', (ev.offsetY + 14) + 'px');
      });
      all.on('mouseleave', function () {
        linkLayer.selectAll('path').attr('stroke-opacity', 0.5).attr('stroke', '#cbd5e1');
        if (tip) tip.style('opacity', 0);
      });
    }

    function expand(node) {
      if (node.expanded) return;
      node.expanded = true;
      fetch('/api/genealogy/expand/' + encodeURIComponent(node.name))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (node.role === 'child') {
            (d.children || []).slice(0, 6).forEach(function (c) {
              var existed = nodes[c.name];
              var n = addNode(c.name, 'grandchild', c.degree, { children_count: c.children_count });
              if (!existed && n) addLink(node, n);
            });
          } else if (node.role === 'parent') {
            (d.parents || []).slice(0, 6).forEach(function (p) {
              var existed = nodes[p.name];
              var n = addNode(p.name, 'grandparent', p.degree);
              if (!existed && n) addLink(n, node);
            });
          }
          draw();
        })
        .catch(function () {});
    }

    function fit() {
      var nl = Object.values(nodes);
      if (!nl.length) return;
      var minX = d3.min(nl, function (n) { return n.x - n.w / 2; });
      var maxX = d3.max(nl, function (n) { return n.x + n.w / 2; });
      var minY = d3.min(nl, function (n) { return n.y - n.h / 2; });
      var maxY = d3.max(nl, function (n) { return n.y + n.h / 2; });
      var bw = (maxX - minX) || 1, bh = (maxY - minY) || 1;
      var sw = svg.node().clientWidth || 900, sh = svg.node().clientHeight || 560;
      var scale = Math.min(sw / (bw + 80), sh / (bh + 80), 1.4);
      var tx = sw / 2 - ((minX + maxX) / 2) * scale;
      var ty = sh / 2 - ((minY + maxY) / 2) * scale;
      svg.transition().duration(500).call(zoom.transform,
        d3.zoomIdentity.translate(tx, ty).scale(scale));
    }

    function shorten(s, n) { s = s || ''; return s.length > n ? s.slice(0, n - 1) + '…' : s; }
    function escapeHtml(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

    draw();
    setTimeout(fit, 80);

    return { reset: fit };
  };
})();
