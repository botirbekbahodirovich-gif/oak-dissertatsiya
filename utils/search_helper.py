"""Universal qidiruv helperi — butun sayt uchun 3 qatlamli qidiruv:

1. Aniq mos (ILIKE) — asl so'rov
2. Alifbo variantlari (kiril<->lotin) — utils.transliterate.get_search_variants
3. Trigram similarity (pg_trgm) — xato harflar / harf almashinuvi uchun
   ("Atajanov" <-> "Atajonov", "Sadiqjon" <-> "Sodiqjon"/"Sadikjon",
   "Hasan" <-> "Xasan"). Trigram o'xshashligi belgi n-gram ustma-ustligiga
   asoslangani uchun bitta-ikkita harf farqini tabiiy ravishda ushlaydi —
   shuning uchun bu yerda alohida fonetik REPLACE() zanjiri QURILMAYDI:
   ILIKE bo'yicha xom ustunni normallashtirilgan "kod"ka solishtirish
   noto'g'ri bo'lar edi (ustunda haqiqiy yozilish bor, kod emas), va
   utils.transliterate ataylab h/x ni ajratadi (himoya/xat kabi so'zlar
   uchun) — buni fonetik jihatdan qo'shib yuborish o'sha ajratishni buzadi.

DB da qidiruv (SQL) uchun: build_search_clause().
Xotiradagi ro'yxat (keshlangan aggregatsiya, masalan advisors/olimlar_catalog)
uchun: fuzzy_score() / matches_query() — Python-side, pg_trgm'siz muhitda ham
ishlaydi (difflib asosida, trigram semantikasiga yaqin).
"""
import difflib

from utils.transliterate import get_search_variants

# Nomlarda keng tarqalgan o'zbekcha yozilish farqlari — pure-Python "kod"ga
# keltirish uchun (hozircha faqat phonetic_normalize() orqali ochiladi;
# SQL ILIKE qatlamida ishlatilmaydi — sababi yuqorida).
PHONETIC_RULES = [
    ('yo', 'o'), ('yu', 'u'), ('ya', 'a'), ('ye', 'e'),
    ('dj', 'j'), ('kh', 'x'), ('sh', 's'), ('ch', 's'),
    ('ё', 'о'), ('ю', 'у'), ('я', 'а'), ('щ', 'ш'),
    ('o', 'a'), ('о', 'а'),
    ('i', 'y'), ('и', 'ы'),
    ('q', 'k'), ('қ', 'к'),
    ('x', 'h'), ('х', 'ҳ'),
    ('ғ', 'г'),
    ("'", ''), ('`', ''), ('ʻ', ''), ('ʼ', ''),
]


def phonetic_normalize(text):
    """Nomni fonetik "kod"ga keltiradi (bir xil talaffuz -> bir xil kod).

    Atajanov -> atajanav; Atajonov -> atajanav (bir xil).
    """
    if not text:
        return ''
    result = text.lower().strip()
    for src, dst in PHONETIC_RULES:
        result = result.replace(src, dst)
    return result


def build_search_clause(query, columns, use_fuzzy=True, fuzzy_threshold=0.3):
    """SQL WHERE/ORDER BY qismlarini quradi.

    Returns (where_sql, where_params, order_sql, order_params) — 4 ta qiymat,
    ataylab ajratilgan, chunki ko'p joyda avval alohida COUNT(*) so'rovi
    (faqat WHERE, ORDER BY'siz), keyin sahifalangan SELECT (WHERE + ORDER BY)
    yuboriladi. Ikkalasini bitta birlashtirilgan params ro'yxati bilan
    ifodalab bo'lmaydi — COUNT so'rovida ORDER BY yo'q, shuning uchun uning
    %s placeholderlari ham yo'q.

    Ishlatilishi:
        where, params, order, order_params = build_search_clause(q, ['title', 'org'])
        cur.execute(f"SELECT COUNT(*) FROM t WHERE {where}", params)
        cur.execute(f"SELECT * FROM t WHERE {where}{order} LIMIT %s OFFSET %s",
                    params + order_params + [per_page, offset])

    use_fuzzy=False — kichik/past-trafikli jadvallarda yoki pg_trgm indeksi
    yo'q ustunlarda similarity() qidiruvini o'chirish uchun (sekin bo'lishi
    mumkin, indekssiz to'liq skan).
    """
    if not query or not query.strip():
        return "TRUE", [], "", []

    query = query.strip()
    variants = get_search_variants(query)  # [asl, kiril<->lotin]

    parts, params = [], []
    for variant in variants:
        for col in columns:
            parts.append(f"{col} ILIKE %s")
            params.append(f"%{variant}%")

    order_parts, order_params = [], []
    if use_fuzzy and len(query) >= 3:
        for variant in variants:
            for col in columns:
                parts.append(f"similarity(lower({col}), lower(%s)) > {fuzzy_threshold}")
                params.append(variant)
                order_parts.append(f"similarity(lower({col}), lower(%s))")
                order_params.append(variant)

    where_sql = "(" + " OR ".join(parts) + ")"
    order_sql = f" ORDER BY GREATEST({', '.join(order_parts)}) DESC" if order_parts else ""
    return where_sql, params, order_sql, order_params


def build_search_clause_simple(query, columns):
    """Sodda versiya — faqat (where_sql, params), fuzzy/order'siz.

    Avtokomplit yoki juda kichik jadvallar uchun (masalan <500 qator)."""
    if not query or not query.strip():
        return "TRUE", []
    variants = get_search_variants(query.strip())
    parts, params = [], []
    for variant in variants:
        for col in columns:
            parts.append(f"{col} ILIKE %s")
            params.append(f"%{variant}%")
    return "(" + " OR ".join(parts) + ")", params


def fuzzy_score(query, text):
    """0..1 oralig'ida o'xshashlik darajasi (difflib — pg_trgm bo'lmagan,
    xotiradagi ro'yxatlar uchun, masalan keshlangan advisors/olimlar
    agregatsiyasi ustida)."""
    if not query or not text:
        return 0.0
    return difflib.SequenceMatcher(None, query.lower(), text.lower()).ratio()


def matches_query(query, *texts, threshold=0.72):
    """query berilgan matnlardan biriga aniq/alifbo-variant/fuzzy mos keladimi.

    Xotiradagi ro'yxat filtrlash uchun (SQL emas) — masalan:
        items = [s for s in items if matches_query(q, s['name'], s['muassasa'])]

    Fuzzy qatlam so'z-so'z solishtiradi (butun matn emas) — aks holda qisqa
    so'rov ("Atajanov") uzun matn ("Отажонов Дилшод ...") bilan to'g'ridan-
    to'g'ri solishtirilsa nisbat sun'iy pasayib, hech qachon threshold'ga
    yetmaydi."""
    if not query or not query.strip():
        return True
    variants = [v.lower() for v in get_search_variants(query.strip())]
    for text in texts:
        if not text:
            continue
        low = text.lower()
        if any(v in low for v in variants):
            return True
        if len(query) >= 3:
            words = low.split()
            if any(fuzzy_score(v, w) >= threshold for v in variants for w in words):
                return True
    return False
