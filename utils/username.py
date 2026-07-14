"""Username (vanity URL) validatsiya va tekshirish.

Slug qoidalari (impersonation/homoglyph oldini olish uchun qat'iy ASCII):
  - 3-30 belgi, faqat [a-z0-9_], harf bilan boshlanadi
  - ketma-ket '__' yo'q, '_' bilan tugamaydi
  - kirilcha/unicode belgilar rad etiladi (faqat ASCII a-z,0-9,_)
  - zaxiralangan so'zlar bloklanadi

Bandlik olim_profiles.slug VA users.slug bo'yicha tekshiriladi (+ 6 oy ichida
bo'shatilmagan username_history eski slug'lari himoyalangan).
"""
import re

# Route/xizmat nomlari bilan to'qnashmasligi + impersonation oldini olish
RESERVED_WORDS = {
    'admin', 'api', 'about', 'cabinet', 'contact', 'courses', 'dashboard',
    'login', 'logout', 'register', 'profile', 'settings', 'search', 'help',
    'support', 'static', 'olim', 'team', 'stats', 'trends', 'clustering',
    'collaboration', 'compare', 'heatmap', 'notifications', 'preparation',
    'sitemap', 'robots', 'offline', 'genealogy', 'top', 'reyting', 'grants',
    'reminders', 'dissertation', 'messages', 'upload', 'analytics', 'data',
    'auth', 'university', 'universities', 'konferensiyalar', 'konferensiya',
    'conferences', 'xarita', 'map', 'moderator', 'official', 'system', 'null',
    'undefined', 'test', 'www', 'mail', 'ftp', 'admin_panel', 'mavzu',
    'swagger', 'graphql', 'webhook', 'callback', 'feed', 'rss', 'bot',
    'crawler', 'scraper', 'olimlar', 'dissertatsiyalar', 'workspace', 'reja',
    'blog', 'yangiliklar', 'vacancies', 'journals', 'councils', 'rahbar',
    'rahbar-topish', 'tadqiqot-xaritasi',
}

_SLUG_RE = re.compile(r'^[a-z][a-z0-9_]*$')
MIN_LEN, MAX_LEN = 3, 30
CHANGE_LIMIT_PER_YEAR = 2       # yilda maksimal o'zgartirish
OLD_SLUG_HOLD_DAYS = 180        # eski slug 6 oy himoyalanadi


def normalize_username(slug):
    """Kiritilgan qiymatni tozalaydi: .strip().lower()."""
    return (slug or '').strip().lower()


def validate_username(slug):
    """(ok: bool, message: str). Faqat format/zaxira tekshiruvi (bandlik alohida)."""
    slug = normalize_username(slug)
    if not slug:
        return False, "Username bo'sh bo'lishi mumkin emas"
    if len(slug) < MIN_LEN:
        return False, f"Kamida {MIN_LEN} ta belgi bo'lishi kerak"
    if len(slug) > MAX_LEN:
        return False, f"Ko'pi bilan {MAX_LEN} ta belgi"
    if not slug[0].isascii() or not slug[0].isalpha():
        return False, "Harf (a-z) bilan boshlanishi kerak"
    if not _SLUG_RE.match(slug):
        return False, ("Faqat kichik lotin harflari, raqamlar va pastki chiziq "
                       "(a-z, 0-9, _). Kirilcha harflar mumkin emas")
    if '__' in slug:
        return False, "Ketma-ket ikkita pastki chiziq mumkin emas"
    if slug.endswith('_'):
        return False, "Pastki chiziq bilan tugashi mumkin emas"
    if slug in RESERVED_WORDS:
        return False, "Bu nom zaxiralangan"
    return True, 'OK'


def is_username_available(slug, exclude_profile_id=None, exclude_user_id=None,
                          cur=None):
    """Slug bo'sh (band emas)mi? olim_profiles.slug + users.slug + himoyalangan
    eski slug'lar (username_history, 6 oy) bo'yicha tekshiradi. `cur` berilmasa
    o'z ulanishini ochadi."""
    slug = normalize_username(slug)
    if not slug:
        return False
    own_conn = None
    if cur is None:
        from data import get_connection
        own_conn = get_connection()
        cur = own_conn.cursor()
    try:
        # olim_profiles.slug
        if exclude_profile_id is not None:
            cur.execute("SELECT 1 FROM olim_profiles WHERE slug = %s AND id <> %s",
                        (slug, exclude_profile_id))
        else:
            cur.execute("SELECT 1 FROM olim_profiles WHERE slug = %s", (slug,))
        if cur.fetchone():
            return False
        # users.slug
        if exclude_user_id is not None:
            cur.execute("SELECT 1 FROM users WHERE slug = %s AND id <> %s",
                        (slug, exclude_user_id))
        else:
            cur.execute("SELECT 1 FROM users WHERE slug = %s", (slug,))
        if cur.fetchone():
            return False
        # himoyalangan eski slug (6 oy) — o'zi egasi bo'lmasa band.
        # OLD_SLUG_HOLD_DAYS — ishonchli modul konstantasi (int), inline xavfsiz.
        try:
            sql = (f"SELECT 1 FROM username_history WHERE old_slug = %s "
                   f"AND changed_at > NOW() - INTERVAL '{int(OLD_SLUG_HOLD_DAYS)} days'")
            if exclude_profile_id is not None:
                cur.execute(sql + " AND profile_id <> %s", (slug, exclude_profile_id))
            else:
                cur.execute(sql, (slug,))
            if cur.fetchone():
                return False
        except Exception:
            pass    # username_history hali yo'q bo'lsa — e'tiborsiz
        return True
    finally:
        if own_conn is not None:
            own_conn.close()


def _translit(s):
    """Ism → ASCII slug bo'lagi (kirill→lotin, faqat [a-z0-9])."""
    try:
        from institutions import transliterate
        s = transliterate(s or '')
    except Exception:
        pass
    s = (s or '').lower()
    for ch in "'ʻʼ‘’`":
        s = s.replace(ch, '')
    return re.sub(r'[^a-z0-9]', '', s)


def generate_username_suggestion(first_name, last_name):
    """3-5 ta band bo'lmagan taklif: familiya_ism, ism_familiya, familiya_i, ...
    Bandlik tekshiriladi; toza namzedlar qaytadi (bo'sh bo'lsa raqamli variant)."""
    f = _translit(first_name)
    l = _translit(last_name)
    cands = []

    def _add(x):
        x = normalize_username(x)
        ok, _ = validate_username(x)
        if ok and x not in cands:
            cands.append(x)

    if l and f:
        _add(f'{l}_{f}')
        _add(f'{f}_{l}')
        _add(f'{l}_{f[:1]}')
        _add(f'{f[:1]}_{l}')
    if l:
        _add(l)
    if f:
        _add(f)
    # bandlarni tashla, bo'shlaridan 5 tagacha
    out = []
    try:
        from data import get_connection
        conn = get_connection()
        try:
            cur = conn.cursor()
            for c in cands:
                if is_username_available(c, cur=cur):
                    out.append(c)
                if len(out) >= 5:
                    break
            # kam bo'lsa — raqamli variant qo'sh
            base = cands[0] if cands else (l or f or 'olim')
            n = 1
            while len(out) < 3 and n < 100:
                c = f'{base}{n}'[:MAX_LEN]
                ok, _ = validate_username(c)
                if ok and is_username_available(c, cur=cur) and c not in out:
                    out.append(c)
                n += 1
        finally:
            conn.close()
    except Exception:
        # DB yo'q — bandlik tekshiruvsiz format-to'g'ri namzedlar
        out = cands[:5]
    return out[:5]
