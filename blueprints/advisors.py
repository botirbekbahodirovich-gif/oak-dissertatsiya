"""Rahbar sahifalari — ilmiy rahbarlar katalogi va profillari (advisors_bp).

Ma'lumot manbai — dissertations jadvalidan hisoblangan agregatsiya (~27K qator,
GROUP BY ilmiy_rahbar). Qimmat so'rov, shuning uchun natija extensions.cache da
10 daqiqa keshlanadi (`_advisor_index`).

Routes:
  GET /rahbar-topish    — katalog: qidiruv, ixtisoslik filtri, saralash, sahifalash.
  GET /rahbar/<slug>    — rahbar profili: shogirdlar timeline (yil bo'yicha),
                          ixtisoslik taqsimoti, muassasalar, shajara havolasi,
                          "Hamkorlik taklifi yuborish" CTA (ro'yxatdan o'tgan
                          bo'lsa Konstruktor taklifiga, bo'lmasa taklif havolasi).

Slug — ismdan deterministik (kirill→lotin, apostroflar tushiriladi); to'qnashuvda
-2, -3 qo'shiladi. Har bir korpusdagi rahbar indeksda bor, shuning uchun boshqa
modullar (mavzu tahlili) slug'ni advisor_slug() orqali xavfsiz quradi.
"""
import re

from flask import Blueprint, render_template, request, abort
from flask_login import current_user

from extensions import cache
from data import get_connection

try:
    import psycopg2.extras as psycopg2_extras
except Exception:  # pragma: no cover — psycopg2 always present in prod
    psycopg2_extras = None

advisors_bp = Blueprint('advisors', __name__)

PER_PAGE = 24
INDEX_CACHE_TTL = 600  # 10 daqiqa — spec bo'yicha qimmat agregatsiya keshi

# sana TEXT ustunidan 4 xonali yil ('2024-05-16' ham, '16.05.2024' ham)
_YEAR_SQL = "substring(sana FROM '((19|20)[0-9]{2})')"
_CODE_RE = re.compile(r'\d{2}\.\d{2}\.\d{2}')

# Kirill → lotin (slug uchun yetarli soddalashtirilgan jadval).
_CYR = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'j', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
    'ъ': '', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', 'ў': 'o', 'қ': 'q',
    'ғ': 'g', 'ҳ': 'h', 'і': 'i', 'ї': 'yi', 'є': 'ye',
}
_APOS = "'ʻʼ‘’`"


def slugify_name(name):
    """Ismdan URL-slug: 'Aliyev B.A.' → 'aliyev-b-a'. Bo'sh natija → 'rahbar'."""
    s = ' '.join((name or '').split()).lower()
    out = []
    for ch in s:
        if ch in _APOS:
            continue
        out.append(_CYR.get(ch, ch))
    s = re.sub(r'[^a-z0-9]+', '-', ''.join(out)).strip('-')
    return s or 'rahbar'


def _fetch_advisor_rows():
    """GROUP BY agregatsiya — har bir rahbar bo'yicha statistika (bitta so'rov)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT TRIM(ilmiy_rahbar) AS name,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE daraja ILIKE '%%PhD%%'
                                           OR daraja ILIKE '%%falsafa%%') AS phd_count,
                       COUNT(*) FILTER (WHERE daraja ILIKE '%%DSc%%'
                                           OR daraja ILIKE '%%fan doktori%%') AS dsc_count,
                       MAX(NULLIF(TRIM(COALESCE(ilmiy_rahbar_daraja, '')), '')) AS daraja,
                       MAX(TRIM(sana)) AS last_sana,
                       MAX(({_YEAR_SQL})::int) AS last_year,
                       array_agg(DISTINCT TRIM(ixtisoslik))
                           FILTER (WHERE TRIM(COALESCE(ixtisoslik, '')) <> '') AS specialties,
                       array_agg(DISTINCT TRIM(muassasa))
                           FILTER (WHERE TRIM(COALESCE(muassasa, '')) <> '') AS institutions,
                       array_agg(DISTINCT ({_YEAR_SQL})::int)
                           FILTER (WHERE {_YEAR_SQL} IS NOT NULL) AS years
                FROM dissertations
                WHERE ilmiy_rahbar IS NOT NULL AND TRIM(ilmiy_rahbar) <> ''
                GROUP BY TRIM(ilmiy_rahbar)
            """)
            return cur.fetchall()
    finally:
        conn.close()


def _advisor_index():
    """{'advisors': [dict, …] (faollik bo'yicha saralangan), 'by_slug': {slug: dict}}.

    10 daqiqa keshlanadi — katalog, profil va tahlil integratsiyasi shu
    indeksdan o'qiydi.
    """
    cached = cache.get('advisor_index')
    if cached is not None:
        return cached
    advisors, by_slug = [], {}
    try:
        rows = _fetch_advisor_rows()
    except Exception:
        rows = []
    for r in rows:
        name = r['name']
        a = {
            'name': name,
            'daraja': (r.get('daraja') or '').strip(),
            'total': int(r.get('total') or 0),
            'phd_count': int(r.get('phd_count') or 0),
            'dsc_count': int(r.get('dsc_count') or 0),
            'last_sana': (r.get('last_sana') or '').strip(),
            'last_year': r.get('last_year') or 0,
            'specialties': sorted(r.get('specialties') or []),
            'institutions': sorted(r.get('institutions') or []),
            'years': sorted(r.get('years') or []),
        }
        a['codes'] = sorted({m.group(0) for s in a['specialties']
                             for m in [_CODE_RE.search(s)] if m})
        slug = slugify_name(name)
        if slug in by_slug:  # to'qnashuv — deterministik -2, -3 …
            n = 2
            while f"{slug}-{n}" in by_slug:
                n += 1
            slug = f"{slug}-{n}"
        a['slug'] = slug
        by_slug[slug] = a
        advisors.append(a)
    advisors.sort(key=lambda a: (a['last_year'], a['total']), reverse=True)
    index = {'advisors': advisors, 'by_slug': by_slug}
    cache.set('advisor_index', index, timeout=INDEX_CACHE_TTL)
    return index


def advisor_slug(name):
    """Ism → slug (indeks orqali; topilmasa None). Boshqa modullar uchun."""
    target = ' '.join((name or '').split())
    if not target:
        return None
    base = slugify_name(target)
    by_slug = _advisor_index()['by_slug']
    low = target.lower()

    def _matches(a):
        return a and ' '.join(a['name'].split()).lower() == low

    a = by_slug.get(base)
    if _matches(a):
        return base
    # to'qnashuv suffikslarini tekshirish
    n = 2
    while f"{base}-{n}" in by_slug:
        if _matches(by_slug[f"{base}-{n}"]):
            return f"{base}-{n}"
        n += 1
    return base if a else None


# ── Katalog ──────────────────────────────────────────────────────────────────

@advisors_bp.route('/rahbar-topish')
def advisor_directory():
    q = ' '.join((request.args.get('q') or '').split()).lower()
    code = (request.args.get('ixtisoslik') or '').strip()
    sort = request.args.get('sort') or 'recent'
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1

    advisors = _advisor_index()['advisors']
    if q:
        advisors = [a for a in advisors if q in a['name'].lower()]
    if code:
        low = code.lower()
        advisors = [a for a in advisors
                    if any(low in s.lower() for s in a['specialties'])]
    if sort == 'students':
        advisors = sorted(advisors, key=lambda a: a['total'], reverse=True)
    elif sort == 'name':
        advisors = sorted(advisors, key=lambda a: a['name'].lower())
    # default 'recent' — indeks tartibining o'zi (last_year, total DESC)

    total = len(advisors)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, pages)
    items = advisors[(page - 1) * PER_PAGE: page * PER_PAGE]

    # Filtr uchun eng ko'p uchraydigan ixtisoslik shifrlari (top 20)
    code_counts = {}
    for a in _advisor_index()['advisors']:
        for c in a['codes']:
            code_counts[c] = code_counts.get(c, 0) + 1
    top_codes = sorted(code_counts, key=lambda c: -code_counts[c])[:20]

    return render_template('rahbar_topish.html',
                           items=items, total=total, page=page, pages=pages,
                           q=request.args.get('q') or '', code=code, sort=sort,
                           top_codes=top_codes)


# ── Profil ───────────────────────────────────────────────────────────────────

def _find_registered_user(cur, name):
    """Rahbar ismini platforma foydalanuvchisiga moslashtiradi (fuzzy).

    1) olim_profiles.olim_name (kabinetga bog'langan) → cabinet_users.email →
       users — kabinetning o'zi ishlatadigan bog'lanish;
    2) users.username to'g'ridan-to'g'ri mos kelsa.
    Returns (user_id, username) yoki None."""
    cur.execute("""
        SELECT u.id, u.username
        FROM olim_profiles p
        JOIN cabinet_users cu ON cu.id = p.cabinet_user_id
        JOIN users u ON LOWER(u.email) = LOWER(cu.email)
        WHERE LOWER(TRIM(p.olim_name)) = LOWER(TRIM(%s))
        LIMIT 1
    """, (name,))
    row = cur.fetchone()
    if row:
        return row
    cur.execute("SELECT id, username FROM users WHERE LOWER(username) = LOWER(TRIM(%s)) LIMIT 1",
                (name,))
    return cur.fetchone()


@advisors_bp.route('/rahbar/<slug>')
def advisor_profile(slug):
    advisor = _advisor_index()['by_slug'].get(slug)
    if not advisor:
        abort(404)
    name = advisor['name']

    registered = None
    students = []
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, sana, daraja, olim, mavzu, ixtisoslik, muassasa, link
                FROM dissertations
                WHERE LOWER(TRIM(ilmiy_rahbar)) = LOWER(%s)
                ORDER BY sana DESC
            """, (name,))
            students = cur.fetchall()
        with conn.cursor() as cur:
            try:
                registered = _find_registered_user(cur, name)
            except Exception:
                registered = None  # olim_profiles/cabinet_users hali yo'q bo'lsa
    finally:
        conn.close()

    # Timeline: yil bo'yicha guruhlash (yangi yillar birinchi)
    year_re = re.compile(r'(19|20)\d{2}')
    by_year, unknown = {}, []
    for s in students:
        m = year_re.search(s.get('sana') or '')
        if m:
            by_year.setdefault(int(m.group(0)), []).append(s)
        else:
            unknown.append(s)
    timeline = sorted(by_year.items(), key=lambda kv: -kv[0])
    max_year_count = max((len(v) for _, v in timeline), default=0)

    # Ixtisoslik taqsimoti
    spec_counts = {}
    for s in students:
        key = (s.get('ixtisoslik') or '').strip()
        if key:
            spec_counts[key] = spec_counts.get(key, 0) + 1
    spec_breakdown = sorted(spec_counts.items(), key=lambda kv: -kv[1])

    meta_description = (
        f"{name} — {advisor['total']} ta shogird himoya qilgan, "
        f"{len(spec_breakdown)} ta ixtisoslikda faol ilmiy rahbar. "
        f"Shogirdlari, yo'nalishlari va muassasalari — Olimlar.uz")

    return render_template('rahbar_profile.html',
                           a=advisor, students=students, timeline=timeline,
                           unknown_year=unknown, max_year_count=max_year_count,
                           spec_breakdown=spec_breakdown,
                           registered=registered,
                           meta_description=meta_description,
                           is_authenticated=getattr(current_user, 'is_authenticated', False))
