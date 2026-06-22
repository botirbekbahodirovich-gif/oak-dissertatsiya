/* Research Heatmap — pure SVG map of Uzbekistan regions, no external map libs.
   Reads window.HEATMAP_DATA (array of region aggregates) and renders an
   interactive, color-coded map with tooltip + click-to-detail panel. */
(function () {
  var DATA = window.HEATMAP_DATA || [];
  var byName = {};
  DATA.forEach(function (r) { byName[r.name] = r; });

  // Schematic (approximate) polygon outlines for each region — viewBox 1000x620.
  var PATHS = {
    "Qoraqalpog'iston": "M60,60 L360,40 L380,200 L300,240 L280,300 L150,320 L80,200 Z",
    "Xorazm":           "M240,300 L320,288 L334,344 L270,362 L238,340 Z",
    "Navoiy":           "M360,200 L560,170 L600,320 L470,360 L380,300 L360,240 Z",
    "Buxoro":           "M300,302 L382,300 L470,362 L440,442 L330,442 L300,382 Z",
    "Samarqand":        "M560,300 L642,300 L662,360 L600,382 L560,360 Z",
    "Jizzax":           "M600,250 L680,250 L690,322 L640,332 L610,300 Z",
    "Sirdaryo":         "M690,258 L742,258 L747,322 L700,322 Z",
    "Toshkent":         "M700,178 L822,160 L852,242 L780,272 L710,250 Z",
    "Qashqadaryo":      "M560,382 L662,370 L692,462 L580,482 L540,422 Z",
    "Surxondaryo":      "M662,462 L722,450 L742,562 L662,582 L640,500 Z",
    "Namangan":         "M820,180 L902,180 L912,230 L840,242 Z",
    "Andijon":          "M900,232 L962,242 L952,302 L890,290 Z",
    "Farg'ona":         "M838,250 L912,250 L922,312 L850,322 Z"
  };
  var LABELS = {
    "Qoraqalpog'iston": [210, 165], "Xorazm": [285, 330], "Navoiy": [470, 262],
    "Buxoro": [372, 385], "Samarqand": [600, 342], "Jizzax": [645, 294],
    "Sirdaryo": [718, 296], "Toshkent": [778, 218], "Qashqadaryo": [612, 432],
    "Surxondaryo": [690, 522], "Namangan": [866, 212], "Andijon": [924, 274],
    "Farg'ona": [882, 292]
  };

  function colorFor(c) {
    if (c >= 2000) return '#60a5fa';
    if (c >= 500) return '#3b82f6';
    if (c >= 100) return '#2563eb';
    return '#1e3a5f';
  }

  var SVGNS = 'http://www.w3.org/2000/svg';
  var svg = document.getElementById('uz-map');
  var tooltip = document.getElementById('map-tooltip');
  if (!svg) return;

  function showTooltip(e, name) {
    var r = byName[name] || { total: 0 };
    tooltip.innerHTML = '<b>' + name + '</b><br>' + (r.total || 0) + ' ta dissertatsiya';
    tooltip.style.display = 'block';
    tooltip.style.left = (e.clientX + 14) + 'px';
    tooltip.style.top = (e.clientY + 14) + 'px';
  }
  function hideTooltip() { tooltip.style.display = 'none'; }

  Object.keys(PATHS).forEach(function (name) {
    var r = byName[name] || { total: 0 };
    var p = document.createElementNS(SVGNS, 'path');
    p.setAttribute('d', PATHS[name]);
    p.setAttribute('fill', colorFor(r.total || 0));
    p.setAttribute('stroke', '#0f172a');
    p.setAttribute('stroke-width', '1.5');
    p.setAttribute('data-region', name);
    p.style.cursor = 'pointer';
    p.addEventListener('mousemove', function (e) { showTooltip(e, name); });
    p.addEventListener('mouseleave', hideTooltip);
    p.addEventListener('click', function () { showDetail(name); });
    svg.appendChild(p);

    var pos = LABELS[name];
    if (pos) {
      var t = document.createElementNS(SVGNS, 'text');
      t.setAttribute('x', pos[0]);
      t.setAttribute('y', pos[1]);
      t.textContent = name;
      svg.appendChild(t);
    }
  });

  // ── Region detail panel ──
  var sparkChart = null;
  function rowList(items) {
    if (!items || !items.length) return '<li><span style="color:#64748b">Ma\'lumot yo\'q</span></li>';
    return items.map(function (it) {
      return '<li><span>' + it.name + '</span><span>' + it.count + '</span></li>';
    }).join('');
  }

  function showDetail(name) {
    var r = byName[name];
    if (!r) return;
    var panel = document.getElementById('region-detail');
    document.getElementById('rd-name').textContent = r.name;
    document.getElementById('rd-total').textContent = r.total;
    document.getElementById('rd-phd').textContent = r.phd;
    document.getElementById('rd-dsc').textContent = r.dsc;
    document.getElementById('rd-unis').innerHTML = rowList(r.top_universities);
    document.getElementById('rd-specs').innerHTML = rowList(r.top_specialties);
    panel.style.display = 'block';

    var years = r.years || [];
    var canvas = document.getElementById('region-spark');
    if (canvas && window.Chart) {
      if (sparkChart) sparkChart.destroy();
      sparkChart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
          labels: years.map(function (y) { return y.year; }),
          datasets: [{
            data: years.map(function (y) { return y.count; }),
            borderColor: '#60a5fa', backgroundColor: 'rgba(96,165,250,0.18)',
            fill: true, tension: 0.35, pointRadius: 2, borderWidth: 2
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#64748b', font: { size: 9 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, grid: { display: false } },
            y: { ticks: { color: '#64748b', font: { size: 9 } }, grid: { color: '#28344a' } }
          }
        }
      });
    }
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
})();
