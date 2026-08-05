/* Olimlar.uz — Chart.js uchun yagona light-theme sozlamalari va palitra.
   Har bir sahifada alohida rang massivi yozmang; shu yerdagi
   OlimlarCharts.palette() dan foydalaning.

   Qiymatlar olimlar-design-system.css tokenlariga mos keladi. Chart.js
   CSS o'zgaruvchilarini canvas ichida o'qiy olmagani uchun ular bu yerda
   takrorlanadi — token o'zgarsa, shu fayl ham yangilansin. */
(function (global) {
  'use strict';

  var TOKENS = {
    textPrimary: '#0c0f12',
    textSecondary: '#333333',
    textMuted: '#666666',
    border: '#e6e6e6',
    borderSubtle: '#ecebe9',
    white: '#ffffff',
    actionBlue: '#0056d6',
    brandOrange: '#ff4203',
    success: '#0c8930',
    error: '#af1d1d',
    notice: '#ff9900'
  };

  /* Kategoriya palitrasi — oq fonda bir-biridan ajraladi, neon emas.
     Tartib ataylab qo'shni ranglar kontrast bo'ladigan qilib tanlangan. */
  var CATEGORICAL = [
    '#0056d6', // action blue
    '#ff4203', // brand orange
    '#0c8930', // success green
    '#7a3ea3', // violet
    '#00808c', // teal
    '#af1d1d', // error red
    '#8a5200', // amber dark
    '#3d5a80', // slate blue
    '#5c8001', // olive
    '#a3357e'  // magenta
  ];

  function palette(count) {
    if (!count || count <= CATEGORICAL.length) {
      return CATEGORICAL.slice(0, count || CATEGORICAL.length);
    }
    var out = [];
    for (var i = 0; i < count; i++) out.push(CATEGORICAL[i % CATEGORICAL.length]);
    return out;
  }

  /* Chart.js global defaultlari — light fon uchun. */
  function applyDefaults() {
    if (typeof global.Chart === 'undefined') return false;
    var C = global.Chart;
    C.defaults.color = TOKENS.textMuted;
    C.defaults.borderColor = TOKENS.border;
    C.defaults.font.family = "'Inter', system-ui, -apple-system, sans-serif";
    C.defaults.font.size = 12;

    if (C.defaults.plugins) {
      if (C.defaults.plugins.legend && C.defaults.plugins.legend.labels) {
        C.defaults.plugins.legend.labels.color = TOKENS.textSecondary;
        C.defaults.plugins.legend.labels.usePointStyle = true;
        C.defaults.plugins.legend.labels.boxWidth = 10;
      }
      if (C.defaults.plugins.title) {
        C.defaults.plugins.title.color = TOKENS.textPrimary;
      }
      if (C.defaults.plugins.tooltip) {
        // Yuqori kontrastli tooltip — oq fon ustida to'q panel.
        C.defaults.plugins.tooltip.backgroundColor = TOKENS.textPrimary;
        C.defaults.plugins.tooltip.titleColor = TOKENS.white;
        C.defaults.plugins.tooltip.bodyColor = TOKENS.white;
        C.defaults.plugins.tooltip.borderColor = TOKENS.textPrimary;
        C.defaults.plugins.tooltip.borderWidth = 0;
        C.defaults.plugins.tooltip.padding = 10;
        C.defaults.plugins.tooltip.cornerRadius = 4;
        C.defaults.plugins.tooltip.displayColors = true;
      }
    }
    return true;
  }

  /* O'q (axis) uchun umumiy sozlama — grid och kulrang, label muted. */
  function axis(extra) {
    var base = {
      grid: { color: TOKENS.borderSubtle, drawBorder: false },
      ticks: { color: TOKENS.textMuted }
    };
    if (extra) {
      Object.keys(extra).forEach(function (k) { base[k] = extra[k]; });
    }
    return base;
  }

  applyDefaults();
  // Chart.js defer bilan yuklansa — DOM tayyor bo'lganda qayta urinamiz.
  if (typeof global.Chart === 'undefined') {
    global.addEventListener('DOMContentLoaded', applyDefaults);
  }

  global.OlimlarCharts = {
    tokens: TOKENS,
    palette: palette,
    axis: axis,
    applyDefaults: applyDefaults
  };
})(window);
