"""WCAG contrast check for the design-system colour tokens.

Reads every --color-* custom property from static/css/olimlar-design-system.css
(resolving var() references), then checks the text/background pairs the UI
actually uses. AA: 4.5:1 for body text, 3:1 for large text and UI parts
(borders of controls, icons, focus rings).

  python scripts/check_contrast_tokens.py          # table + exit 1 on failure
  python scripts/check_contrast_tokens.py --quiet  # failures only

Pairs are declared in PAIRS below; a new text/background combination in the
UI should be added here so it is checked.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_CSS = os.path.join(ROOT, 'static', 'css', 'olimlar-design-system.css')

AA_TEXT = 4.5
AA_LARGE = 3.0

# (foreground, background, minimum ratio, where it is used)
PAIRS = [
    # body text on the three neutral surfaces
    *[(fg, bg, AA_TEXT, 'matn')
      for fg in ('text-primary', 'text-secondary', 'text-muted')
      for bg in ('surface', 'background', 'background-soft')],
    # links / actions as text
    *[(fg, bg, AA_TEXT, 'havola')
      for fg in ('action', 'action-hover')
      for bg in ('surface', 'background', 'background-soft', 'action-soft')],
    # white label on filled buttons and badges
    *[('white', bg, AA_TEXT, 'to\'ldirilgan tugma / badge')
      for bg in ('action', 'action-hover', 'action-pressed', 'premium', 'error', 'success')],
    # status text on its own soft background and on white
    ('error', 'error-soft', AA_TEXT, 'status badge'),
    ('error', 'surface', AA_TEXT, 'xato matni'),
    ('success-ink', 'success-soft', AA_TEXT, 'status badge'),
    ('success', 'surface', AA_TEXT, 'muvaffaqiyat matni'),
    ('warning-ink', 'notice-soft', AA_TEXT, 'status badge'),
    ('warning-ink', 'surface', AA_TEXT, 'ogohlantirish matni'),
    ('text-primary', 'notice', AA_TEXT, 'notice to\'ldirilgan badge'),
    ('brand-ink', 'orange-soft', AA_TEXT, 'brand badge'),
    ('brand-ink', 'surface', AA_TEXT, 'brand matni'),
    # non-text UI parts (WCAG 1.4.11)
    ('action', 'surface', AA_LARGE, 'fokus halqasi'),
    # #c98a16 (notice) oq fonda 2.95:1 — faqat to'ldirish rangi; ikonka/matn = warning-ink
    ('warning-ink', 'surface', AA_LARGE, 'ogohlantirish ikonka'),
    ('success', 'surface', AA_LARGE, 'muvaffaqiyat ikonka'),
    ('brand', 'surface', AA_LARGE, 'brand aksent'),
]

_VAR = re.compile(r'--(color-[a-z0-9-]+)\s*:\s*([^;]+);')


def load_tokens(path=TOKEN_CSS):
    """{'color-x': '#rrggbb'} with var() chains resolved (last definition wins)."""
    raw = {}
    with open(path, encoding='utf-8') as fh:
        css = re.sub(r'/\*.*?\*/', '', fh.read(), flags=re.S)
    for name, value in _VAR.findall(css):
        raw[name] = value.strip()

    def resolve(name, depth=0):
        value = raw.get(name)
        if value is None or depth > 10:
            return None
        m = re.fullmatch(r'var\(--(color-[a-z0-9-]+)\)', value)
        return resolve(m.group(1), depth + 1) if m else value

    return {name: resolve(name) for name in raw}


def to_rgb(value):
    value = (value or '').strip().lower()
    if value in ('white', '#fff'):
        return (255, 255, 255)
    m = re.fullmatch(r'#([0-9a-f]{6})', value)
    if not m:
        return None
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg, bg):
    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def check(tokens=None):
    """[(fg, bg, value_fg, value_bg, ratio or None, minimum, use, ok)]."""
    tokens = tokens if tokens is not None else load_tokens()
    tokens.setdefault('color-white', '#ffffff')
    rows = []
    for fg, bg, minimum, use in PAIRS:
        vf = tokens.get('color-' + fg) if fg != 'white' else '#ffffff'
        vb = tokens.get('color-' + bg)
        rf, rb = to_rgb(vf), to_rgb(vb)
        r = ratio(rf, rb) if rf and rb else None
        rows.append((fg, bg, vf, vb, r, minimum, use, r is not None and r >= minimum))
    return rows


def main(argv=None):
    quiet = '--quiet' in (argv if argv is not None else sys.argv[1:])
    rows = check()
    failed = [r for r in rows if not r[7]]
    for fg, bg, vf, vb, r, minimum, use, ok in rows:
        if quiet and ok:
            continue
        shown = f'{r:5.2f}' if r is not None else '  n/a'
        print(f"{'OK  ' if ok else 'FAIL'} {shown}:1 (≥{minimum}) "
              f"--color-{fg} {vf} on --color-{bg} {vb}  [{use}]")
    print(f'{len(rows) - len(failed)}/{len(rows)} pairs pass WCAG AA')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
