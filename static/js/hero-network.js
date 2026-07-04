/* hero-network.js — bosh sahifa "olimlar tarmog'i" fon animatsiyasi.
   Kutubxonasiz, ≤6KB. Ekrandan tashqarida va document.hidden da pauza,
   prefers-reduced-motion da bitta statik kadr. */
(function () {
  'use strict';

  function init() {
    var canvas = document.getElementById('hx-net');
    if (!canvas || canvas.dataset.hxInit) return; /* ikki marta init bo'lmasin */
    canvas.dataset.hxInit = '1';

    var ctx = canvas.getContext('2d');
    if (!ctx) return;

    var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var mobile = window.innerWidth < 768;
    var COUNT = mobile ? 35 : 70;
    var LINK = 130;              /* chiziq masofasi, px */
    var MOUSE_R = 150;           /* sichqonchaga tortilish radiusi */
    var dpr = Math.min(window.devicePixelRatio || 1, 2);

    var w = 0, h = 0, pts = [], raf = null, visible = true, pageHidden = false;
    var mouse = { x: -9999, y: -9999 };

    function resize() {
      var r = canvas.parentElement.getBoundingClientRect();
      w = Math.max(1, r.width);
      h = Math.max(1, r.height);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function seed() {
      pts = [];
      for (var i = 0; i < COUNT; i++) {
        pts.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.3,  /* drift ±0.15px/kadr */
          vy: (Math.random() - 0.5) * 0.3,
          r: 1.5 + Math.random()            /* 1.5–2.5px */
        });
      }
    }

    function step(draw) {
      ctx.clearRect(0, 0, w, h);
      var i, j, p, q, dx, dy, d;
      for (i = 0; i < pts.length; i++) {
        p = pts[i];
        if (draw !== false) {
          p.x += p.vx; p.y += p.vy;
          /* sichqoncha yaqinida yumshoq tortilish (faqat desktop) */
          if (!mobile) {
            dx = mouse.x - p.x; dy = mouse.y - p.y;
            d = dx * dx + dy * dy;
            if (d < MOUSE_R * MOUSE_R && d > 1) {
              d = Math.sqrt(d);
              p.x += (dx / d) * 0.25;
              p.y += (dy / d) * 0.25;
            }
          }
          if (p.x < -10) p.x = w + 10; else if (p.x > w + 10) p.x = -10;
          if (p.y < -10) p.y = h + 10; else if (p.y > h + 10) p.y = -10;
        }
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, 6.2832);
        ctx.fillStyle = 'rgba(147,197,253,0.7)';
        ctx.fill();
      }
      for (i = 0; i < pts.length; i++) {
        for (j = i + 1; j < pts.length; j++) {
          p = pts[i]; q = pts[j];
          dx = p.x - q.x; dy = p.y - q.y;
          if (dx > LINK || dx < -LINK || dy > LINK || dy < -LINK) continue;
          d = Math.sqrt(dx * dx + dy * dy);
          if (d >= LINK) continue;
          var o = (1 - d / LINK) * 0.18;
          /* kursor yaqinidagi chiziqlar biroz yorqinroq */
          if (!mobile) {
            var mx = (p.x + q.x) / 2 - mouse.x, my = (p.y + q.y) / 2 - mouse.y;
            if (mx * mx + my * my < MOUSE_R * MOUSE_R) o = Math.min(0.34, o * 1.9);
          }
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.strokeStyle = 'rgba(147,197,253,' + o.toFixed(3) + ')';
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }
    }

    function loop() {
      raf = null;
      if (!visible || pageHidden) return;
      step(true);
      raf = requestAnimationFrame(loop);
    }
    function play() { if (!raf && visible && !pageHidden && !reduced) raf = requestAnimationFrame(loop); }
    function pause() { if (raf) { cancelAnimationFrame(raf); raf = null; } }

    resize();
    seed();

    if (reduced) { step(false); return; } /* bitta statik kadr, animatsiya yo'q */

    /* hero ekrandan chiqsa — to'xtatamiz */
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (entries) {
        visible = entries[0].isIntersecting;
        if (visible) play(); else pause();
      }, { threshold: 0 }).observe(canvas);
    }
    document.addEventListener('visibilitychange', function () {
      pageHidden = document.hidden;
      if (pageHidden) pause(); else play();
    });

    if (!mobile) {
      canvas.parentElement.addEventListener('mousemove', function (e) {
        var r = canvas.getBoundingClientRect();
        mouse.x = e.clientX - r.left;
        mouse.y = e.clientY - r.top;
      }, { passive: true });
      canvas.parentElement.addEventListener('mouseleave', function () {
        mouse.x = -9999; mouse.y = -9999;
      }, { passive: true });
    }

    var rt = null;
    window.addEventListener('resize', function () { /* debounce 150ms */
      if (rt) clearTimeout(rt);
      rt = setTimeout(function () {
        mobile = window.innerWidth < 768;
        resize();
        seed();
        if (reduced) step(false);
      }, 150);
    });

    play();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
