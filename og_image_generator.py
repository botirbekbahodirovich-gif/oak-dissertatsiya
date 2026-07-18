"""Dinamik Open Graph rasm generatsiyasi — "Mening ilmiy daraxtim" ulashish
kartochkasi uchun (1200×630, Telegram/Facebook/LinkedIn standart o'lchami).

Pillow bilan dark-tema brend kartochka chizadi:
  · chapda — olim ismi, ilmiy darajasi/lavozimi, tashkiloti
  · o'ngda — soddalashtirilgan ilmiy shajara vizuali (rahbar↑ olim· shogird↓)
  · yuqori-o'ngda — olimlar.uz domeni
  · pastda — "N ta shogird • N ta avlod • N ta himoya" statistikasi

Kesh: har bir olim uchun rasm diskda saqlanadi. Fayl nomiga statistika imzosi
(signature) qo'shiladi — ma'lumot o'zgarsa (yangi shogird), yangi fayl yaraladi
va eski nusxalar tozalanadi. Generatsiya xato bersa — default brend rasmi.

Public API:
    get_og_image(name, stats, cache_dir, fallback_path) -> (path, is_fallback)
    render_card(name, stats, out_path)   # past-daraja chizuvchi
"""
import hashlib
import math
import os
import random
import re

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:  # Pillow o'rnatilmagan bo'lsa — modul import bo'ladi, lekin fallback ishlaydi
    _PIL_OK = False

W, H = 1200, 630

# ── Brend ranglari ──────────────────────────────────────────────────────────
BG_TOP = (11, 18, 32)       # #0b1220
BG_BOT = (17, 28, 51)       # #111c33
WHITE = (241, 245, 249)     # #f1f5f9
MUTED = (148, 163, 184)     # #94a3b8
FAINT = (71, 85, 105)       # #475569
BLUE = (59, 130, 246)       # #3b82f6
BLUE_DEEP = (30, 64, 175)   # #1e40af
GREEN = (5, 150, 105)       # #059669  (PhD)
ORANGE = (227, 100, 3)      # #e36403  (DSc)
ACCENT = (147, 187, 252)    # #93bbfc

_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    os.path.join(os.path.dirname(__file__), "static", "fonts"),
]
_FONT_CACHE = {}


def _font(size, bold=False):
    """DejaVu Sans (Kirill+Lotin qamrovi keng); topilmasa PIL default."""
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    fname = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    f = None
    for d in _FONT_DIRS:
        p = os.path.join(d, fname)
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                break
            except Exception:
                f = None
    if f is None:
        try:
            f = ImageFont.truetype(fname, size)  # tizim font-path'idan
        except Exception:
            f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _clean(text):
    """Uzbek modifikator harflarini (oʻ, gʻ — U+02BB/02BC) oddiy apostrofga
    almashtiradi, aks holda DejaVu'da 'tofu' bo'lib chiqishi mumkin."""
    s = str(text or "")
    s = re.sub(r"[ʻʼ‘’‛`´]", "'", s)
    return s.strip()


def _text_w(draw, text, font):
    try:
        return draw.textlength(text, font=font)
    except Exception:
        b = draw.textbbox((0, 0), text, font=font)
        return b[2] - b[0]


def _wrap(draw, text, font, max_w, max_lines=2):
    """So'z bo'yicha o'rash; oxirgi qatorni '…' bilan qisqartirish."""
    words = _clean(text).split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if _text_w(draw, trial, font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    # oxirgi qator sig'masa — kesib '…'
    if lines:
        last = lines[-1]
        if _text_w(draw, last, font) > max_w:
            while last and _text_w(draw, last + "…", font) > max_w:
                last = last[:-1]
            lines[-1] = last + "…"
    return lines or [""]


def _initials(name):
    parts = [p for p in _clean(name).split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:1].upper()
    return (parts[0][:1] + parts[1][:1]).upper()


def _vertical_gradient(top, bottom):
    base = Image.new("RGB", (W, H), top)
    top_r, top_g, top_b = top
    bot_r, bot_g, bot_b = bottom
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / (H - 1)
        grad.putpixel((0, y), (
            int(top_r + (bot_r - top_r) * t),
            int(top_g + (bot_g - top_g) * t),
            int(top_b + (bot_b - top_b) * t),
        ))
    return base.paste(grad.resize((W, H))) or base


def _draw_particles(img, seed):
    """Fon uchun yumshoq 'particle network' — nuqta va yaqin nuqtalar orasidagi
    xira chiziqlar. Seed olim nomidan — bir olimga har safar bir xil fon."""
    rnd = random.Random(seed)
    n = 46
    pts = [(rnd.uniform(0, W), rnd.uniform(0, H)) for _ in range(n)]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i in range(n):
        for j in range(i + 1, n):
            dx = pts[i][0] - pts[j][0]
            dy = pts[i][1] - pts[j][1]
            d = math.hypot(dx, dy)
            if d < 190:
                a = int(28 * (1 - d / 190))
                ld.line([pts[i], pts[j]], fill=(96, 165, 250, a), width=1)
    for (x, y) in pts:
        r = rnd.choice([1.5, 2, 2.5])
        ld.ellipse([x - r, y - r, x + r, y + r], fill=(96, 165, 250, 70))
    img.paste(layer, (0, 0), layer)


def _node(draw, cx, cy, r, label, fill, stroke, sw=3, txt_col=WHITE, fsize=None):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=stroke, width=sw)
    f = _font(fsize or int(r * 0.7), bold=True)
    tw = _text_w(draw, label, f)
    try:
        asc, desc = f.getmetrics()
        th = asc + desc
    except Exception:
        th = int(r)
    draw.text((cx - tw / 2, cy - th / 2), label, font=f, fill=txt_col)


def _draw_tree(draw, name, n_students, cx=930):
    """O'ng tomonda soddalashtirilgan shajara: 2 rahbar↑, markaz olim, 3 shogird↓."""
    center = (cx, H // 2 - 6)
    parents = [(cx - 120, 168), (cx + 120, 168)]
    n_children = max(1, min(3, n_students or 1))
    child_xs = {1: [cx], 2: [cx - 105, cx + 105], 3: [cx - 150, cx, cx + 150]}[n_children]
    children = [(x, 470) for x in child_xs]

    # bog'lovchi chiziqlar (avval, tugun ostida qolishi uchun)
    for p in parents:
        draw.line([p, center], fill=(71, 85, 105), width=3)
    for c in children:
        draw.line([center, c], fill=(71, 85, 105), width=3)

    # rahbarlar (yuqori) — ko'k
    for p in parents:
        _node(draw, p[0], p[1], 30, "🎓" if False else "", (30, 58, 138), BLUE, sw=2)
        _node(draw, p[0], p[1], 30, "•", (30, 58, 138), BLUE, sw=2, fsize=22)
    # shogirdlar (past) — yashil (PhD ohang)
    for c in children:
        _node(draw, c[0], c[1], 30, "•", (22, 32, 50), GREEN, sw=2, fsize=22)
    # markaz — olim (initsiallar, ko'k, glow taqlidi ikki halqa)
    draw.ellipse([center[0] - 58, center[1] - 58, center[0] + 58, center[1] + 58],
                 outline=(59, 130, 246, 60), width=2)
    _node(draw, center[0], center[1], 48, _initials(name), BLUE_DEEP, BLUE, sw=4,
          fsize=34)


def render_card(name, stats, out_path):
    """Kartochkani chizib `out_path`ga saqlaydi. PIL bo'lmasa — ValueError."""
    if not _PIL_OK:
        raise RuntimeError("Pillow mavjud emas")

    name = _clean(name) or "Noma'lum olim"
    degree = _clean(stats.get("degree") or "")
    position = _clean(stats.get("position") or "")
    institution = _clean(stats.get("institution") or "")
    n_students = int(stats.get("n_students") or 0)
    n_generations = int(stats.get("n_generations") or 0)
    n_defended = int(stats.get("n_defended") or 0)

    img = _vertical_gradient(BG_TOP, BG_BOT)
    _draw_particles(img, seed=hashlib.md5(name.encode("utf-8")).hexdigest())
    draw = ImageDraw.Draw(img)

    # chap ustun uchun chegara
    LX = 70
    RIGHT_LIMIT = 720  # matn shajaradan oldin tugaydi

    # yuqori-o'ng: domen
    dom_f = _font(30, bold=True)
    dom = "olimlar.uz"
    dw = _text_w(draw, dom, dom_f)
    draw.ellipse([W - 70 - dw - 42, 44, W - 70 - dw - 8, 78], fill=BLUE)
    draw.text((W - 70 - dw - 34, 49), "🌳" if False else "O", font=_font(24, bold=True), fill=WHITE)
    draw.text((W - 70 - dw, 46), dom, font=dom_f, fill=ACCENT)

    # yuqori-chap: kichik yorliq
    tag_f = _font(24, bold=True)
    draw.text((LX, 60), "ILMIY SHAJARA DARAXTI", font=tag_f, fill=BLUE)

    # ism (katta, 2 qatorgacha)
    name_f = _font(62, bold=True)
    name_lines = _wrap(draw, name, name_f, RIGHT_LIMIT - LX, max_lines=2)
    y = 150
    for ln in name_lines:
        draw.text((LX, y), ln, font=name_f, fill=WHITE)
        y += 74

    # daraja / lavozim
    y += 6
    sub_f = _font(30, bold=False)
    degree_full = {"PhD": "PhD — falsafa doktori", "DSc": "DSc — fan doktori"}.get(degree, degree)
    line2 = " · ".join(x for x in [degree_full, position] if x)
    if line2:
        for ln in _wrap(draw, line2, sub_f, RIGHT_LIMIT - LX, max_lines=2):
            draw.text((LX, y), ln, font=sub_f, fill=MUTED)
            y += 40

    # tashkilot
    if institution:
        inst_f = _font(26, bold=False)
        y += 4
        for ln in _wrap(draw, institution, inst_f, RIGHT_LIMIT - LX, max_lines=2):
            draw.text((LX, y), ln, font=inst_f, fill=FAINT)
            y += 34

    # o'ng: shajara vizuali
    _draw_tree(draw, name, n_students)

    # pastki statistika chizig'i
    draw.line([(LX, H - 96), (W - 70, H - 96)], fill=(30, 41, 59), width=2)
    stat_f = _font(30, bold=True)
    lbl_f = _font(20, bold=False)
    items = [
        (str(n_students), "ta shogird"),
        (str(n_generations), "ta ilmiy avlod"),
        (str(n_defended), "ta himoya"),
    ]
    sx = LX
    for val, lbl in items:
        draw.text((sx, H - 74), val, font=stat_f, fill=ACCENT)
        vw = _text_w(draw, val, stat_f)
        draw.text((sx + vw + 8, H - 68), lbl, font=lbl_f, fill=MUTED)
        sx += vw + 8 + _text_w(draw, lbl, lbl_f) + 46

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def _safe_key(name):
    h = hashlib.md5(_clean(name).lower().encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", _clean(name).lower())[:40].strip("-")
    return (slug + "-" + h) if slug else h


def _signature(stats):
    """Statistika imzosi — o'zgarsa keshni yangilash uchun."""
    raw = "|".join(str(stats.get(k) or "") for k in
                   ("degree", "position", "institution",
                    "n_students", "n_generations", "n_defended"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def get_og_image(name, stats, cache_dir, fallback_path=None):
    """Keshlangan rasm yo'lini qaytaradi (kerak bo'lsa generatsiya qiladi).

    Returns (path, is_fallback). Xato bo'lsa — (fallback_path, True) yoki
    fallback ham bo'lmasa RuntimeError.
    """
    key = _safe_key(name)
    sig = _signature(stats)
    fname = f"{key}.{sig}.png"
    out_path = os.path.join(cache_dir, fname)

    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path, False

    try:
        os.makedirs(cache_dir, exist_ok=True)
        # eski imzoli nusxalarni tozalash
        try:
            for f in os.listdir(cache_dir):
                if f.startswith(key + ".") and f != fname:
                    try:
                        os.remove(os.path.join(cache_dir, f))
                    except OSError:
                        pass
        except OSError:
            pass
        render_card(name, stats, out_path)
        return out_path, False
    except Exception:
        if fallback_path and os.path.exists(fallback_path):
            return fallback_path, True
        raise
