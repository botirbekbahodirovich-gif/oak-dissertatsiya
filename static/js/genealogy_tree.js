/* ============================================================
 * High-Performance Academic Genealogy Tree
 * Collapsible SVG tree: node radius scales with direct-student count,
 * a high-contrast badge shows the count, avatars load from Supabase with
 * a typographic initials fallback on 404.
 *
 * Usage:  GenealogyTree.mount('#genealogy-canvas', scholarId);
 * ============================================================ */
(function (global) {
  'use strict';

  var AVATAR_BASE =
    'https://qzbgmfbpryneyacrcdfh.supabase.co/storage/v1/object/public/avatars/';
  var H_GAP = 210;   // horizontal distance between generations (px)
  var V_GAP = 92;    // vertical distance between siblings (px)
  var MIN_R = 22;    // smallest node radius
  var MAX_R = 60;    // largest node radius

  function avatarUrl(name) {
    var p = (name || '').trim().split(/\s+/);
    var last = p[0] || '', first = p[1] || '', patr = p[2] || '';
    return AVATAR_BASE + encodeURIComponent(last + '_' + first + '_' + patr) + '.jpg';
  }

  function initials(name) {
    var p = (name || '').trim().split(/\s+/).filter(Boolean);
    var a = (p[0] || ' ')[0] || '';
    var b = (p[1] || '')[0] || '';
    return (a + b).toUpperCase();
  }

  // Radius scales with direct student count (sqrt keeps area proportional).
  function radiusFor(count, maxCount) {
    if (maxCount <= 0) return MIN_R;
    var t = Math.sqrt(count) / Math.sqrt(maxCount);
    return MIN_R + t * (MAX_R - MIN_R);
  }

  function svgEl(tag, attrs) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) { if (attrs.hasOwnProperty(k)) e.setAttribute(k, attrs[k]); }
    return e;
  }

  function GenealogyTree(container, rootData) {
    this.container = container;
    this.root = rootData;
    this.maxCount = 1;
    this._walk(this.root, function (n) {
      if (n.direct_students > this.maxCount) this.maxCount = n.direct_students;
      // Initial viewport state: collapsed — hide every sub-branch.
      n._collapsed = (n.children && n.children.length > 0);
    }.bind(this));

    this.svg = svgEl('svg', { width: '100%', height: '100%', class: 'gen-tree-svg' });
    this.edges = svgEl('g', { class: 'gen-edges' });
    this.nodesG = svgEl('g', { class: 'gen-nodes' });
    this.viewport = svgEl('g', { class: 'gen-viewport' });
    this.viewport.appendChild(this.edges);
    this.viewport.appendChild(this.nodesG);
    this.svg.appendChild(this.viewport);
    container.innerHTML = '';
    container.appendChild(this.svg);

    this.render();
  }

  GenealogyTree.prototype._walk = function (node, fn) {
    fn(node);
    if (node.children) {
      for (var i = 0; i < node.children.length; i++) this._walk(node.children[i], fn);
    }
  };

  // Assign x/y to every currently-visible node (respecting _collapsed).
  GenealogyTree.prototype._layout = function () {
    var self = this;
    var rowY = 0;
    function place(node, depth) {
      node._x = 40 + depth * H_GAP;
      if (node._collapsed || !node.children || node.children.length === 0) {
        node._y = (rowY += V_GAP);
        return node._y;
      }
      var first = null, last = null;
      for (var i = 0; i < node.children.length; i++) {
        var cy = place(node.children[i], depth + 1);
        if (first === null) first = cy;
        last = cy;
      }
      node._y = (first + last) / 2;   // centre parent over its children
      return node._y;
    }
    place(self.root, 0);
    self._height = rowY + V_GAP;
  };

  GenealogyTree.prototype.render = function () {
    this._layout();
    this.svg.setAttribute('viewBox',
      '0 0 ' + (60 + 6 * H_GAP) + ' ' + Math.max(this._height + 40, 200));

    var edges = this.edges, nodesG = this.nodesG, self = this;
    edges.innerHTML = '';
    nodesG.innerHTML = '';

    function visibleChildren(n) {
      return (!n._collapsed && n.children) ? n.children : [];
    }

    (function draw(node) {
      var kids = visibleChildren(node);
      for (var i = 0; i < kids.length; i++) {
        var c = kids[i];
        var path = svgEl('path', {
          class: 'gen-edge',
          d: 'M' + node._x + ',' + node._y +
             ' C' + (node._x + H_GAP / 2) + ',' + node._y +
             ' ' + (c._x - H_GAP / 2) + ',' + c._y +
             ' ' + c._x + ',' + c._y
        });
        edges.appendChild(path);
        draw(c);
      }
      nodesG.appendChild(self._nodeEl(node));
    })(this.root);
  };

  GenealogyTree.prototype._nodeEl = function (node) {
    var self = this;
    var r = radiusFor(node.direct_students, this.maxCount);
    var g = svgEl('g', {
      class: 'gen-node' + (node._collapsed ? ' is-collapsed' : ''),
      transform: 'translate(' + node._x + ',' + node._y + ')'
    });

    // Clip path so the avatar image stays inside the circle.
    var clipId = 'gclip-' + Math.random().toString(36).slice(2);
    var clip = svgEl('clipPath', { id: clipId });
    clip.appendChild(svgEl('circle', { r: r, cx: 0, cy: 0 }));
    g.appendChild(clip);

    g.appendChild(svgEl('circle', { class: 'gen-node-bg', r: r, cx: 0, cy: 0 }));

    // Typographic initials fallback (shown underneath the image).
    var init = svgEl('text', {
      class: 'gen-node-initials', x: 0, y: 0,
      'text-anchor': 'middle', 'dominant-baseline': 'central',
      'font-size': Math.max(12, r * 0.7)
    });
    init.textContent = initials(node.name);
    g.appendChild(init);

    // Avatar image; hide it on 404 so the initials show through.
    var img = svgEl('image', {
      class: 'gen-node-img', x: -r, y: -r, width: r * 2, height: r * 2,
      'clip-path': 'url(#' + clipId + ')',
      preserveAspectRatio: 'xMidYMid slice',
      href: avatarUrl(node.name)
    });
    img.setAttributeNS('http://www.w3.org/1999/xlink', 'href', avatarUrl(node.name));
    img.addEventListener('error', function () { img.style.display = 'none'; });
    g.appendChild(img);

    // High-contrast direct-student count badge.
    var bx = r * 0.72, by = -r * 0.72;
    g.appendChild(svgEl('circle', { class: 'gen-badge-bg', cx: bx, cy: by, r: 12 }));
    var badge = svgEl('text', {
      class: 'gen-badge-text', x: bx, y: by,
      'text-anchor': 'middle', 'dominant-baseline': 'central', 'font-size': 12
    });
    badge.textContent = node.direct_students;
    g.appendChild(badge);

    // Name label under the node.
    var label = svgEl('text', {
      class: 'gen-node-label', x: 0, y: r + 16, 'text-anchor': 'middle'
    });
    label.textContent = node.name;
    g.appendChild(label);

    // Click toggles expand/collapse (first click expands, second collapses).
    if (node.children && node.children.length > 0) {
      g.style.cursor = 'pointer';
      g.addEventListener('click', function (ev) {
        ev.stopPropagation();
        node._collapsed = !node._collapsed;
        self.render();
      });
    }
    return g;
  };

  GenealogyTree.mount = function (selector, scholarId) {
    var container = (typeof selector === 'string')
      ? document.querySelector(selector) : selector;
    if (!container) return;
    container.classList.add('gen-tree-loading');
    fetch('/api/v1/scholar/' + scholarId + '/tree')
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        container.classList.remove('gen-tree-loading');
        new GenealogyTree(container, data);
      })
      .catch(function (e) {
        container.classList.remove('gen-tree-loading');
        container.innerHTML =
          '<div class="gen-tree-error">Ma\'lumot yuklanmadi: ' + e.message + '</div>';
      });
  };

  global.GenealogyTree = GenealogyTree;
})(window);
