/* Olimlar.uz — Chart.js uchun yagona light-theme sozlamalari va palitra.
   Har bir sahifada alohida rang massivi yozmang; shu yerdagi
   OlimlarCharts.palette() dan foydalaning.

   Canvas ranglari CSS tokenlaridan o‘qiladi; tokenlar yagona manba. */
(function (global) {
  'use strict';

  var css = global.getComputedStyle(document.documentElement);
  function token(name) { return css.getPropertyValue(name).trim(); }
  var TOKENS = {
    textPrimary: token('--color-text-primary'), textSecondary: token('--color-text-secondary'),
    textMuted: token('--color-text-muted'), border: token('--color-border'),
    borderSubtle: token('--color-border-subtle'), white: token('--color-white'),
    actionBlue: token('--color-action-blue'), brandOrange: token('--color-brand-orange'),
    success: token('--color-success'), error: token('--color-error'), notice: token('--color-notice')
  };
  // Ordered blue series; category labels and tooltips carry the meaning.
  var CATEGORICAL = [1,2,3,4,5,6].map(function(n){return token('--color-series-'+n);});

  CATEGORICAL.forEach(function(value, i){ TOKENS['series' + (i + 1)] = value; });

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
