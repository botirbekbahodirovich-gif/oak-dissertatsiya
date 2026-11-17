"""Tizimga kirgan foydalanuvchining olim profili: bosh sahifadagi
"Mening profilim" bloki, tezkor panel havolalari va /api/me/badges sonlari.

Bog'lanish zanjiri (yangi jadval yo'q, mavjud da'vo tizimi ishlatiladi):
    users.email → cabinet_users (LOWER email) → cabinet_users.olim_name
    (bo'lmasa olim_profiles.cabinet_user_id bo'yicha birinchi da'vo).
Da'vo /cabinet/onboarding → /cabinet/api/claim orqali qilinadi va hozircha
tasdiqlanmaydi (keyingi bosqich).

Ism bo'yicha hisoblar (shogirdlar, opponentlik, 2-avlod) aniq
LOWER(TRIM(...)) mosligida — profil sahifasi bilan bir xil qoida. Kirill/lotin
va "o'g'li/qizi" yozilishlari normallashtirilmagani uchun son KAM chiqishi
mumkin; 2-avlod shu sababli "≈N" ko'rinishida beriladi.
"""
import logging
import re
from urllib.parse import quote

log = logging.getLogger(__name__)

STATS_CACHE_TTL = 600     # ism bo'yicha ochiq hisoblar (shogird, reyting, ...)
LINK_CACHE_TTL = 600      # foydalanuvchi → olim nomi (har sahifadagi panel uchun)
DASH = '—'
_PLACEHOLDER_NAME = re.compile(r'^cabinet_\d+$')
_ORCID_ID = re.compile(r'(\d{4}-\d{4}-\d{4}-\d{3}[\dX])', re.IGNORECASE)
_TG_USER = re.compile(r'^(?:https?://)?(?:t\.me|telegram\.me)/@?([A-Za-z0-9_]{3,64})/?$')
_DEGREE_ORDER = {'DSC': 2, 'PHD': 1}

# Bildirishnoma sanog'iga KIRMAYDIGAN turlar: har bir yangi xabar
# diss_notifications'da ham 'new_message' qatorini yaratadi — u xabarlar
# belgisida sanaladi, ikki marta emas.
_NOT_A_NOTIFICATION = ('new_message',)


# ── kichik yordamchilar ─────────────────────────────────────────────────────

def _cache():
    try:
        from extensions import cache
        return cache
    except Exception:
        return None


def _cache_get(key):
    c = _cache()
    try:
        return c.get(key) if c else None
    except Exception:
        return None


def _cache_set(key, value, ttl):
    c = _cache()
    try:
        if c:
            c.set(key, value, timeout=ttl)
    except Exception:
        pass


def _cache_delete(key):
    c = _cache()
    try:
        if c:
            c.delete(key)
    except Exception:
        pass


def _connect():
    from data import get_connection
    return get_connection()


def _safe(cur, label, sql, params=()):
    """Bitta so'rov; jadval yo'q bo'lsa (sxemalar lazy yaratiladi) yoki xato
    bersa tranzaksiyani buzmasdan None qaytaradi."""
    cur.execute('SAVEPOINT ms_q')
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in (cur.description or [])]
        cur.execute('RELEASE SAVEPOINT ms_q')
        return rows, cols
    except Exception as exc:
        log.debug('my_scholar %s failed: %s', label, exc)
        cur.execute('ROLLBACK TO SAVEPOINT ms_q')
        return None, []


def _first_dict(rows, cols):
    return dict(zip(cols, rows[0])) if rows else None


def _clean(v):
    return ' '.join(str(v).split()) if v is not None else ''


def _http_url(v):
    v = (v or '').strip()
    return v if v.lower().startswith(('https://', 'http://')) else ''


def profile_url(olim_name):
    return '/olim/' + quote(olim_name, safe='')


def school_url(olim_name):
    return '/genealogy/' + quote(olim_name, safe='')


# ── 1. foydalanuvchi → olim ────────────────────────────────────────────────

def _resolve(cur, email, cabinet_user_id=None):
    """(cabinet_user_id, olim_name|None, cabinet_row dict|None)."""
    row = None
    if cabinet_user_id:
        rows, cols = _safe(cur, 'cabinet_by_id',
                           "SELECT id, email, telegram_username, olim_name "
                           "FROM cabinet_users WHERE id = %s", (cabinet_user_id,))
        row = _first_dict(rows, cols)
    if not row and email:
        rows, cols = _safe(cur, 'cabinet_by_email',
                           "SELECT id, email, telegram_username, olim_name "
                           "FROM cabinet_users WHERE LOWER(email) = LOWER(%s) "
                           "ORDER BY (olim_name IS NULL OR TRIM(olim_name) = ''), id LIMIT 1",
                           (email,))
        row = _first_dict(rows, cols)
    if not row:
        return None, None, None
    name = _clean(row.get('olim_name'))
    if not name or _PLACEHOLDER_NAME.match(name):
        rows, _ = _safe(cur, 'claimed_profile',
                        "SELECT olim_name FROM olim_profiles WHERE cabinet_user_id = %s "
                        "AND olim_name NOT LIKE 'cabinet\\_%%' ORDER BY id LIMIT 1",
                        (row['id'],))
        name = _clean(rows[0][0]) if rows else ''
    return row['id'], (name or None), row


def scholar_link(user):
    """Har sahifadagi tezkor panel uchun: {'olim_name', 'profile_url',
    'school_url'} yoki None. Foydalanuvchi bo'yicha keshlanadi."""
    uid = getattr(user, 'id', None)
    if not uid or not getattr(user, 'is_authenticated', False):
        return None
    cab_id = _session_cabinet_id()
    key = _link_key(uid, cab_id)
    hit = _cache_get(key)
    if hit is not None:
        return hit or None
    link = {}
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                _, name, _ = _resolve(cur, getattr(user, 'email', '') or '', cab_id)
            conn.rollback()
        finally:
            conn.close()
        if name:
            link = {'olim_name': name, 'profile_url': profile_url(name),
                    'school_url': school_url(name)}
    except Exception as exc:
        log.debug('scholar_link failed: %s', exc)
        return None          # DB xatosi keshlanmaydi
    _cache_set(key, link, LINK_CACHE_TTL)
    return link or None


def _session_cabinet_id():
    try:
        from flask import session
        return session.get('cabinet_user_id')
    except Exception:
        return None


def _link_key(user_id, cabinet_user_id):
    return 'my_scholar_link_v2:%s:%s' % (user_id, cabinet_user_id or '-')


def forget_user(user_id, cabinet_user_id=None):
    """Profil da'vo qilingach yoki o'zgarganda panel keshini tozalaydi."""
    if user_id:
        _cache_delete(_link_key(user_id, cabinet_user_id))
        _cache_delete(_link_key(user_id, None))


# ── 2. o'qilmaganlar ────────────────────────────────────────────────────────

def _badge_counts(cur, user_id):
    msgs, _ = _safe(cur, 'unread_messages', """
        SELECT COUNT(*) FROM messages m
        JOIN conversation_participants cp
             ON cp.conversation_id = m.conversation_id AND cp.user_id = %s
        WHERE m.created_at > cp.last_read_at
          AND m.sender_id <> %s AND m.is_deleted = FALSE
    """, (user_id, user_id))
    diss, _ = _safe(cur, 'unread_diss_notifications',
                    "SELECT COUNT(*) FROM diss_notifications WHERE user_id = %s "
                    "AND is_read = FALSE AND event_type <> ALL(%s)",
                    (user_id, list(_NOT_A_NOTIFICATION)))
    alerts, _ = _safe(cur, 'unread_user_alerts',
                      "SELECT COUNT(*) FROM user_alerts WHERE user_id = %s AND is_read = FALSE",
                      (user_id,))

    def n(rows):
        return int(rows[0][0] or 0) if rows else 0
    return {'messages': n(msgs), 'notifications': n(diss) + n(alerts)}


def badge_counts(user_id):
    """{'messages': n, 'notifications': n} — header, panel va profil bloki
    uchun yagona manba. DB yo'q bo'lsa nollar."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                out = _badge_counts(cur, user_id)
            conn.rollback()
            return out
        finally:
            conn.close()
    except Exception as exc:
        log.debug('badge_counts failed: %s', exc)
        return {'messages': 0, 'notifications': 0}


def _latest_unread_message(cur, user_id):
    rows, _ = _safe(cur, 'latest_unread_message', """
        SELECT COALESCE(u.username, 'Foydalanuvchi'), m.body, m.conversation_id
        FROM messages m
        JOIN conversation_participants cp
             ON cp.conversation_id = m.conversation_id AND cp.user_id = %s
        LEFT JOIN users u ON u.id = m.sender_id
        WHERE m.created_at > cp.last_read_at
          AND m.sender_id <> %s AND m.is_deleted = FALSE
        ORDER BY m.created_at DESC LIMIT 1
    """, (user_id, user_id))
    if not rows:
        return None
    sender, body, conv_id = rows[0]
    text = _clean(body)
    snippet = (text[:60] + '…') if len(text) > 60 else (text or 'Fayl')
    return {'sender': sender, 'snippet': snippet,
            'url': '/messages/%s' % conv_id if conv_id else '/messages'}


# ── 3. ism bo'yicha ochiq hisoblar (keshlanadi) ────────────────────────────

def _specialty_rank(olim_name, code):
    """Ball bo'yicha reyting (blueprints/reyting.py) ichida shu shifr bo'yicha o'rin."""
    if not code:
        return None
    from blueprints.reyting import _build_rating
    from data import clean_olim_name
    key = clean_olim_name(olim_name).lower()
    total, mine = 0, None
    # scholars ro'yxati ball bo'yicha kamayish tartibida keladi.
    for s in _build_rating().get('scholars', []):
        if code in (s.get('codes') or ()):
            total += 1
            if mine is None and s.get('name', '').lower() == key:
                mine = total
    if not mine:
        return None
    return {'rank': mine, 'total': total}


def _scholar_stats(cur, olim_name):
    """Ochiq ma'lumotdan hisoblanadigan raqamlar. Shaxsiy ma'lumot yo'q —
    shuning uchun ism bo'yicha keshlash xavfsiz."""
    key = 'my_scholar_stats_v1:' + olim_name.lower()
    hit = _cache_get(key)
    if hit is not None:
        return hit

    own, cols = _safe(cur, 'own_dissertations',
                      "SELECT daraja, sana, ixtisoslik, COALESCE(ixtisoslik_nomi, ''), "
                      "muassasa, ilmiy_rahbar, mavzu FROM dissertations "
                      "WHERE LOWER(TRIM(olim)) = LOWER(TRIM(%s))", (olim_name,))
    own = own or []
    students, _ = _safe(cur, 'students',
                        "SELECT COUNT(DISTINCT LOWER(TRIM(olim))) FROM dissertations "
                        "WHERE LOWER(TRIM(COALESCE(ilmiy_rahbar, ''))) = LOWER(TRIM(%s))",
                        (olim_name,))
    gen2, _ = _safe(cur, 'second_generation', """
        SELECT COUNT(DISTINCT LOWER(TRIM(g.olim))) FROM dissertations g
        WHERE LOWER(TRIM(COALESCE(g.ilmiy_rahbar, ''))) IN (
              SELECT DISTINCT LOWER(TRIM(s.olim)) FROM dissertations s
              WHERE LOWER(TRIM(COALESCE(s.ilmiy_rahbar, ''))) = LOWER(TRIM(%s))
                AND COALESCE(TRIM(s.olim), '') <> '')
          AND LOWER(TRIM(g.olim)) <> LOWER(TRIM(%s))
    """, (olim_name, olim_name))
    opp, _ = _safe(cur, 'opponent_count', """
        SELECT COUNT(*) FROM dissertations
        WHERE LOWER(TRIM(COALESCE(opponent_1, ''))) = LOWER(TRIM(%s))
           OR LOWER(TRIM(COALESCE(opponent_2, ''))) = LOWER(TRIM(%s))
           OR LOWER(TRIM(COALESCE(opponent_3, ''))) = LOWER(TRIM(%s))
    """, (olim_name, olim_name, olim_name))

    def one(rows):
        return int(rows[0][0] or 0) if rows else None

    # Eng yuqori daraja (DSc > PhD), teng bo'lsa eng so'nggi sana.
    def sana_key(sana):
        m = re.match(r'^(\d{2})\.(\d{2})\.(\d{4})$', (sana or '').strip())
        return (m.group(3) + m.group(2) + m.group(1)) if m else ''
    best = None
    for daraja, sana, code, code_name, muassasa, rahbar, mavzu in own:
        rank = _DEGREE_ORDER.get(_clean(daraja).upper(), 0)
        k = (rank, sana_key(sana))
        if best is None or k > best[0]:
            best = (k, {'daraja': _clean(daraja), 'sana': _clean(sana), 'code': _clean(code),
                        'code_name': _clean(code_name), 'muassasa': _clean(muassasa),
                        'rahbar': _clean(rahbar), 'mavzu': _clean(mavzu)})

    h = None
    try:
        from blueprints.ranking import get_scholar_h_index
        h = get_scholar_h_index(olim_name)
    except Exception as exc:
        log.debug('h-index lookup failed: %s', exc)

    stats = {
        'defense': best[1] if best else None,
        'students': one(students),
        'gen2': one(gen2),
        'opponent': one(opp),
        'h_index': h,
    }
    _cache_set(key, stats, STATS_CACHE_TTL)
    return stats


# ── 4. bosh sahifa bloki ────────────────────────────────────────────────────

def _cell(label, value=None, title=None, href=None, approx=False):
    return {'label': label, 'value': value if value not in (None, '') else DASH,
            'title': title or '', 'href': href or '', 'approx': approx}


def _telegram(profile, cabinet):
    url = _http_url((profile or {}).get('telegram_url'))
    m = _TG_USER.match(url) if url else None
    if m:
        return '@' + m.group(1), 'https://t.me/' + m.group(1)
    tg = _clean((cabinet or {}).get('telegram_username')).lstrip('@')
    if re.fullmatch(r'[A-Za-z0-9_]{3,64}', tg or ''):
        return '@' + tg, 'https://t.me/' + tg
    return None, None


def _email(user, cabinet):
    for e in (getattr(user, 'email', '') or '', (cabinet or {}).get('email') or ''):
        e = e.strip()
        if '@' in e and not e.lower().endswith('@auth.local'):
            return e
    return None


def _orcid(profile, h):
    raw = (profile or {}).get('orcid_url') or (h or {}).get('orcid') or ''
    m = _ORCID_ID.search(raw)
    return (m.group(1).upper(), 'https://orcid.org/' + m.group(1).upper()) if m else (None, None)


def build_home_profile(user):
    """Bosh sahifa bloki uchun ma'lumot.

    None → blok umuman chiqmaydi (mehmon yoki DB xatosi);
    {'status': 'unlinked', ...} → "Profilingizni toping va bog'lang" taklifi;
    {'status': 'linked', ...} → to'liq kartochka.
    """
    if not getattr(user, 'is_authenticated', False):
        return None
    try:
        conn = _connect()
    except Exception as exc:
        log.debug('build_home_profile: no DB: %s', exc)
        return None
    try:
        with conn.cursor() as cur:
            cab_id, name, cabinet = _resolve(cur, getattr(user, 'email', '') or '',
                                             _session_cabinet_id())
            badges = _badge_counts(cur, user.id)
            if not name:
                conn.rollback()
                return {'status': 'unlinked', 'onboarding_url': '/cabinet/onboarding',
                        'badges': badges}
            rows, cols = _safe(cur, 'olim_profile',
                               "SELECT * FROM olim_profiles "
                               "WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s)) LIMIT 1", (name,))
            profile = _first_dict(rows, cols) or {}
            pubs, _ = _safe(cur, 'publications',
                            "SELECT COUNT(*) FROM olim_maqolalar "
                            "WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s))", (name,))
            stats = _scholar_stats(cur, name)
            latest = _latest_unread_message(cur, user.id) if badges['messages'] else None
        conn.rollback()
    except Exception as exc:
        log.warning('build_home_profile failed: %s', exc)
        return None
    finally:
        conn.close()

    return _compose(user, name, profile, cabinet, stats, badges, latest,
                    int(pubs[0][0] or 0) if pubs else 0)


def _compose(user, name, profile, cabinet, stats, badges, latest, pubs):
    from utils.olim_photos import scholar_photo_url
    defense = stats.get('defense') or {}
    h = stats.get('h_index') or {}

    full_name = ' '.join(p for p in (_clean(profile.get('last_name')),
                                     _clean(profile.get('first_name')),
                                     _clean(profile.get('patronymic'))) if p) or name
    degree = _clean(profile.get('academic_degree')) or defense.get('daraja', '')
    rank_title = _clean(profile.get('academic_rank'))
    code = _clean(profile.get('ixtisoslik')) or defense.get('code', '')
    code_name = defense.get('code_name', '') if defense.get('code') == code else ''
    specialty = ('%s (%s)' % (code_name, code) if code_name else code) if code else ''
    institution = _clean(profile.get('institution')) or defense.get('muassasa', '')
    position = _clean(profile.get('position'))

    rating = None
    try:
        rating = _specialty_rank(name, code)
    except Exception as exc:
        log.debug('specialty rank failed: %s', exc)
    from blueprints.reyting import _W_DSC, _W_PHD, _W_RAHBAR, _W_OPP
    rating_title = ("Ball bo'yicha reyting (h-indeks emas): DSc ×%d, PhD ×%d, "
                    "ilmiy rahbarlik ×%d, opponentlik ×%d. Mutaxassislik: %s."
                    % (_W_DSC, _W_PHD, _W_RAHBAR, _W_OPP, code or DASH))
    rating_value = ("%d-o'rin / %d" % (rating['rank'], rating['total'])) if rating else None

    h_value = h.get('h_scopus')
    h_title = 'Manba: uz.h-index.com (Scopus). WoS: %s, Google Scholar: %s.' % (
        h.get('h_wos') if h.get('h_wos') is not None else DASH,
        h.get('h_scholar') if h.get('h_scholar') is not None else DASH) if h else \
        "uz.h-index.com ro'yxatida topilmadi."

    gen2 = stats.get('gen2')
    gen2_value = ('≈%d' % gen2) if gen2 else (0 if gen2 == 0 else None)
    gen2_title = ("Taxminiy: shogirdlar ismlari ilmiy rahbar ismi bilan aniq moslab "
                  "sanalgan; yozilish farqlari (kirill/lotin) tufayli kam chiqishi mumkin. "
                  "Shajarani ochish.")

    defense_value = ', '.join(p for p in (defense.get('daraja'), defense.get('sana')) if p) or None

    tg_text, tg_href = _telegram(profile, cabinet)
    email = _email(user, cabinet)
    orcid_text, orcid_href = _orcid(profile, h)
    scholar_href = _http_url(profile.get('scholar_url'))

    p_url = profile_url(name)
    s_url = school_url(name)
    rows = [
        [_cell('Reyting (mutaxassislik)', rating_value, rating_title, '/reyting/ball'),
         _cell('h-indeks', h_value, h_title),
         _cell('Iqtiboslar', None, "Ishonchli iqtiboslar manbasi hozircha ulanmagan."),
         _cell('Nashrlar', pubs or None,
               "Kabinetda kiritilgan maqolalar soni." if pubs else
               "Kabinetda maqola kiritilmagan.", '/cabinet' if not pubs else '')],
        [_cell('Shogirdlar', stats.get('students'),
               "OAK bazasida ilmiy rahbar sifatida himoya qilgan shogirdlar (ism bo'yicha)."),
         _cell('Shogirdlarning shogirdlari', gen2_value, gen2_title, s_url, approx=bool(gen2)),
         _cell('Opponentlik', stats.get('opponent'),
               "OAK bazasidagi rasmiy opponentliklar soni (ism bo'yicha)."),
         _cell('Himoya', defense_value, defense.get('mavzu', ''))],
        [_cell('Telegram', tg_text, href=tg_href),
         _cell('Email', email, href=('mailto:' + email) if email else ''),
         _cell('ORCID', orcid_text, href=orcid_href),
         _cell('Google Scholar', 'profil' if scholar_href else None, href=scholar_href)],
    ]
    compact = [rows[0][0], rows[0][1], rows[1][0],
               dict(rows[1][1], label='2-avlod')]

    return {
        'status': 'linked',
        'olim_name': name,
        'name': full_name,
        'degree_line': ' · '.join(p for p in (', '.join(x for x in (degree, rank_title) if x),
                                              specialty) if p),
        'org_line': ', '.join(p for p in (institution, position) if p),
        'compact_line': ' · '.join(p for p in (degree, specialty, institution) if p),
        'interests': [],      # olim_profiles.research_interests — migratsiya 025 (keyingi bosqich)
        'photo_url': scholar_photo_url(name, profile.get('photo_url')),
        'profile_url': p_url,
        'school_url': s_url,
        'edit_url': '/cabinet',
        'rows': rows,
        'compact': compact,
        'badges': badges,
        'latest_message': latest,
    }
