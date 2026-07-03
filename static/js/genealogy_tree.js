/* ============================================================================
 * Ilmiy shajara — interactive academic genealogy tree (D3 v7).
 *
 * window.ShajaraTree.mount({ container, name, tooltip, onEmpty, onReady })
 *   - fetches /api/genealogy/<name> (full recursive descendant tree),
 *   - renders a collapsible left→right tree where node radius scales with the
 *     scholar's direct-student count, a high-contrast badge shows that count,
 *     avatars load from Supabase (initials fallback on 404), and branches
 *     expand/collapse on click with smooth animation.
 *   - Nodes flagged has_more (children cut by the server-side cap) lazy-load
 *     their immediate students from /api/genealogy/expand/<name> on click.
 *   returns { reset } to re-center + re-collapse to the initial view.
 * ========================================================================== */
(function (global) {
  'use strict';

  var MIN_R = 25, MAX_R = 60;      // node radius bounds (px)
  var LEVEL_GAP = 260;             // horizontal distance between generations
  var DURATION = 450;              // expand/collapse animation (ms)
  var FALLBACK_COLORS = ['#3b82f6', '#059669', '#e36403', '#8b5cf6', '#ec4899', '#0ea5e9'];

  function degColor(deg) {
    return deg === 'DSc' ? '#e36403' : deg === 'PhD' ? '#059669' : '#3b82f6';
  }
  function initials(name) {
    var p = (name || '').trim().split(/\s+/).filter(Boolean);
    return (((p[0] || ' ')[0] || '') + ((p[1] || '')[0] || '')).toUpperCase();
  }
  function fallbackColor(name) {
    var h = 0; name = name || '';
    for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return FALLBACK_COLORS[h % FALLBACK_COLORS.length];
  }
  function shortName(name) {
    name = name || '';
    return name.length > 22 ? name.slice(0, 21) + '…' : name;
  }
  function escapeHtml(s) {
    var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML;
  }
  var ROLE_UZ = { DSc: 'DSc — fan doktori', PhD: 'PhD — falsafa doktori' };

  function render(container, data, tipEl) {
    container.innerHTML = '';
    var W = container.clientWidth || 900;
    var H = container.clientHeight || 560;

    var svg = d3.select(container).append('svg')
      .attr('class', 'shajara-svg')
      .attr('width', '100%').attr('height', '100%')
      .attr('viewBox', '0 0 ' + W + ' ' + H);
    var defs = svg.append('defs');
    // Soft glow for the root node.
    var glow = defs.append('filter').attr('id', 'sh-glow')
      .attr('x', '-60%').attr('y', '-60%').attr('width', '220%').attr('height', '220%');
    glow.append('feDropShadow').attr('dx', 0).attr('dy', 0).attr('stdDeviation', 6)
      .attr('flood-color', '#3b82f6').attr('flood-opacity', 0.85);

    var viewport = svg.append('g').attr('class', 'sh-viewport');
    var gLink = viewport.append('g').attr('class', 'sh-links');
    var gNode = viewport.append('g').attr('class', 'sh-nodes');

    var zoom = d3.zoom().scaleExtent([0.2, 2.5]).on('zoom', function (ev) {
      viewport.attr('transform', ev.transform);
    });
    svg.call(zoom).on('dblclick.zoom', null);

    var i = 0;
    var root = d3.hierarchy(data, function (d) { return d.children; });
    root.x0 = H / 2; root.y0 = 0;

    // Radius scale keyed to the largest direct-student count in the tree.
    var maxDirect = 1;
    root.each(function (d) {
      var n = (d.data.direct_students || 0);
      if (n > maxDirect) maxDirect = n;
    });
    var rScale = d3.scaleSqrt().domain([0, maxDirect]).range([MIN_R, MAX_R]);
    function radius(d) { return rScale(d.data.direct_students || 0); }

    var treeLayout = d3.tree().nodeSize([2 * MAX_R + 26, LEVEL_GAP]);

    // Collapsed state: root shows only its immediate children on load.
    function collapse(d) {
      if (d.children) { d._children = d.children; d._children.forEach(collapse); d.children = null; }
    }
    function collapseToInitial() {
      if (root.children) root.children.forEach(collapse);
    }
    collapseToInitial();

    function hasHidden(d) {
      return !!d._children || (!!d.data.has_more && !d.children && !d._children);
    }

    function tipShow(ev, d) {
      if (!tipEl) return;
      var bits = [];
      if (d.data.degree) bits.push(ROLE_UZ[d.data.degree] || d.data.degree);
      bits.push((d.data.direct_students || 0) + ' ta bevosita shogird');
      tipEl.innerHTML = '<b>' + escapeHtml(d.data.name) + '</b><br>' + bits.join('<br>');
      tipEl.style.opacity = '1';
      tipMove(ev);
    }
    function tipMove(ev) {
      if (!tipEl) return;
      var rect = container.getBoundingClientRect();
      tipEl.style.left = (ev.clientX - rect.left + 14) + 'px';
      tipEl.style.top = (ev.clientY - rect.top + 14) + 'px';
    }
    function tipHide() { if (tipEl) tipEl.style.opacity = '0'; }

    function clicked(ev, d) {
      ev.stopPropagation();
      if (d.children) {                       // expanded → collapse
        d._children = d.children; d.children = null; update(d);
      } else if (d._children) {               // collapsed → expand
        d.children = d._children; d._children = null; update(d);
      } else if (d.data.has_more) {           // truncated leaf → lazy-load
        lazyLoad(d);
      } else {                                 // real leaf → open profile
        window.location.href = '/olim/' + encodeURIComponent(d.data.name);
      }
    }

    function lazyLoad(d) {
      d.data.has_more = false;
      fetch('/api/genealogy/expand/' + encodeURIComponent(d.data.name))
        .then(function (r) { return r.json(); })
        .then(function (res) {
          var kids = (res && res.children) || [];
          if (kids.length) {
            d.data.children = kids;
            kids.forEach(function (cd) {
              var child = d3.hierarchy(cd);
              child.parent = d;
              child.depth = d.depth + 1;
              child.x0 = d.x; child.y0 = d.y;
              (d.children || (d.children = [])).push(child);
            });
          }
          update(d);
        })
        .catch(function () { update(d); });
    }

    var diagonal = d3.linkHorizontal().x(function (d) { return d.y; }).y(function (d) { return d.x; });

    function update(source) {
      var treeData = treeLayout(root);
      var nodes = treeData.descendants();
      var links = treeData.descendants().slice(1);
      nodes.forEach(function (d) { d.y = d.depth * LEVEL_GAP; });

      // ---- NODES ----
      var node = gNode.selectAll('g.sh-node').data(nodes, function (d) { return d.id || (d.id = ++i); });

      var nodeEnter = node.enter().append('g')
        .attr('class', 'sh-node')
        .attr('transform', function () { return 'translate(' + source.y0 + ',' + source.x0 + ')'; })
        .attr('cursor', 'pointer')
        .style('opacity', 0)
        .on('click', clicked)
        .on('mouseenter', tipShow).on('mousemove', tipMove).on('mouseleave', tipHide);

      nodeEnter.each(function (d) {
        var r = radius(d);
        var g = d3.select(this);
        var cid = 'sh-clip-' + d.id;
        defs.append('clipPath').attr('id', cid).append('circle').attr('r', r);

        g.append('circle').attr('class', 'sh-bg').attr('r', r)
          .attr('fill', fallbackColor(d.data.name));
        // Initials sit behind the avatar; visible if the photo 404s.
        g.append('text').attr('class', 'sh-init')
          .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
          .attr('font-size', Math.max(12, r * 0.8)).attr('fill', '#fff')
          .text(initials(d.data.name));
        if (d.data.avatar) {
          g.append('image').attr('class', 'sh-avatar')
            .attr('href', d.data.avatar).attr('xlink:href', d.data.avatar)
            .attr('x', -r).attr('y', -r).attr('width', 2 * r).attr('height', 2 * r)
            .attr('clip-path', 'url(#' + cid + ')')
            .attr('preserveAspectRatio', 'xMidYMid slice')
            .on('error', function () { d3.select(this).remove(); });
        }
        // Direct-student count badge (top-right, orange).
        if ((d.data.direct_students || 0) > 0) {
          var bx = r * 0.72, by = -r * 0.72;
          g.append('circle').attr('class', 'sh-badge-bg').attr('cx', bx).attr('cy', by).attr('r', 12);
          g.append('text').attr('class', 'sh-badge-t').attr('x', bx).attr('y', by)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .text(d.data.direct_students);
        }
        g.append('text').attr('class', 'sh-label')
          .attr('text-anchor', 'middle').attr('y', r + 16).text(shortName(d.data.name));
      });

      var nodeUpdate = nodeEnter.merge(node);
      nodeUpdate.transition().duration(DURATION)
        .attr('transform', function (d) { return 'translate(' + d.y + ',' + d.x + ')'; })
        .style('opacity', 1);

      // Degree colour ring; collapsed / has-more → orange dashed indicator.
      nodeUpdate.select('circle.sh-bg')
        .attr('stroke', function (d) { return hasHidden(d) ? '#f59e0b' : degColor(d.data.degree); })
        .attr('stroke-width', function (d) { return d.depth === 0 ? 4 : 2.5; })
        .attr('stroke-dasharray', function (d) { return hasHidden(d) ? '5 4' : null; })
        .style('filter', function (d) { return d.depth === 0 ? 'url(#sh-glow)' : null; });

      var nodeExit = node.exit().transition().duration(DURATION)
        .attr('transform', function () { return 'translate(' + source.y + ',' + source.x + ')'; })
        .style('opacity', 0).remove();
      nodeExit.each(function (d) { defs.select('#sh-clip-' + d.id).remove(); });

      // ---- LINKS ----
      var link = gLink.selectAll('path.sh-link').data(links, function (d) { return d.id; });

      var linkEnter = link.enter().insert('path', 'g').attr('class', 'sh-link')
        .attr('d', function () {
          var o = { x: source.x0, y: source.y0 };
          return diagonal({ source: o, target: o });
        });

      linkEnter.merge(link).transition().duration(DURATION)
        .attr('d', function (d) { return diagonal({ source: d.parent, target: d }); });

      link.exit().transition().duration(DURATION)
        .attr('d', function () {
          var o = { x: source.x, y: source.y };
          return diagonal({ source: o, target: o });
        })
        .remove();

      nodes.forEach(function (d) { d.x0 = d.x; d.y0 = d.y; });
    }

    function fit() {
      var nodes = root.descendants();
      var minX = d3.min(nodes, function (d) { return d.x; }) - MAX_R;
      var maxX = d3.max(nodes, function (d) { return d.x; }) + MAX_R;
      var minY = d3.min(nodes, function (d) { return d.y; }) - MAX_R;
      var maxY = d3.max(nodes, function (d) { return d.y; }) + MAX_R + 60;
      var bw = (maxY - minY) || 1, bh = (maxX - minX) || 1;
      var scale = Math.min(W / bw, H / bh, 1.1);
      var tx = (W - scale * (minY + maxY)) / 2;
      var ty = (H - scale * (minX + maxX)) / 2;
      svg.transition().duration(DURATION)
        .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    }

    update(root);
    setTimeout(fit, DURATION + 40);

    function expandAll(d) {
      if (d._children) { d.children = d._children; d._children = null; }
      if (d.children) d.children.forEach(expandAll);
    }

    return {
      reset: function () {
        // Re-expand everything, then collapse back to the initial view + re-center.
        expandAll(root);
        collapseToInitial();
        update(root);
        setTimeout(fit, DURATION + 40);
      }
    };
  }

  function resolveEl(x) { return typeof x === 'string' ? document.querySelector(x) : x; }

  var ShajaraTree = {
    mount: function (opts) {
      var container = resolveEl(opts.container);
      if (!container) return;
      var tipEl = opts.tooltip ? resolveEl(opts.tooltip) : null;
      var name = opts.name;
      container.classList.add('shajara-loading');
      fetch('/api/genealogy/' + encodeURIComponent(name))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          container.classList.remove('shajara-loading');
          if (data && data.error) throw new Error(data.error);
          var hasTree = data && data.children && data.children.length > 0;
          if (!hasTree) { if (opts.onEmpty) opts.onEmpty(); return; }
          var api = render(container, data, tipEl);
          if (opts.onReady) opts.onReady(api);
        })
        .catch(function (e) {
          container.classList.remove('shajara-loading');
          if (opts.onEmpty) opts.onEmpty(e);
          else container.innerHTML = '<div class="shajara-error">Ma\'lumot yuklanmadi.</div>';
        });
    }
  };

  global.ShajaraTree = ShajaraTree;
})(window);
