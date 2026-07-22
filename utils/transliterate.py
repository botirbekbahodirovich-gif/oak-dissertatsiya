"""O'zbek lotin <-> kiril transliteratsiyasi — qidiruv uchun.

29 000+ dissertatsiya bazasi kiril alifbosida saqlangan, lekin foydalanuvchilar
ko'pincha lotin alifbosida qidiradi. Bu modul qidiruv so'zining ikkinchi
(transliteratsiya qilingan) variantini hosil qiladi, shunda ILIKE ikkala
alifbo bo'yicha ham moslikni topadi. Faqat qidiruv uchun — case-insensitive
(natijalar kichik harfda), aniq/qaytariladigan transliteratsiya emas.

Faqat stdlib, tashqi kutubxona kerak emas.
"""
import re

# Foydalanuvchilar o' / g' harflaridagi tutuq belgisini turlicha yozadi
# (', ', `, ʻ, ʼ, ´ va h.k.) — barchasini bitta shaklga keltiramiz.
_APOSTROPHE_RE = re.compile("[‘’ʻʼ`´]")


def _normalize_apostrophe(text):
    return _APOSTROPHE_RE.sub("'", text)


# Lotin -> kiril, digraflar (2+ harfli birikmalar) avval — tartib muhim,
# aks holda masalan "sh" "s" + "h" bo'lib noto'g'ri aylantiriladi.
# Eslatma: "h" -> "ҳ" va "x" -> "х" ATAYLAB ikki xil harf — o'zbek tilida bular
# boshqa-boshqa tovushlar/harflar ("himoya"/ҳимоя va "xat"/хат), aralashtirib
# yuborilsa juda ko'p qidiruv (himoya, huquq, hujjat...) natija bermay qoladi.
_LOTIN_TO_KIRIL = [
    ("ch", "ч"), ("sh", "ш"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"),
    ("o'", "ў"), ("g'", "ғ"),
    ("a", "а"), ("b", "б"), ("d", "д"), ("e", "е"), ("f", "ф"),
    ("g", "г"), ("h", "ҳ"), ("i", "и"), ("j", "ж"), ("k", "к"),
    ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"),
    ("q", "қ"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"),
    ("v", "в"), ("x", "х"), ("y", "й"), ("z", "з"),
]

# Kiril -> lotin — yuqoridagi jadvalning aynan teskarisi (barcha kiril
# qiymatlar bir xil, to'qnashuv yo'q — h/x ajratilgani tufayli).
_KIRIL_TO_LOTIN = {kir: lat for lat, kir in _LOTIN_TO_KIRIL}


def lotin_to_kiril(text):
    """Lotin matnni kirilga aylantiradi (qidiruv uchun — natija kichik harfda)."""
    if not text:
        return text
    t = _normalize_apostrophe(text).lower()
    for lat, kir in _LOTIN_TO_KIRIL:
        t = t.replace(lat, kir)
    return t


def kiril_to_lotin(text):
    """Kiril matnni lotinga aylantiradi (qidiruv uchun — natija kichik harfda)."""
    if not text:
        return text
    return "".join(_KIRIL_TO_LOTIN.get(ch, ch) for ch in text.lower())


def get_search_variants(query):
    """Qidiruv so'zining kiril va lotin variantlarini qaytaradi.

    Asl so'z har doim birinchi. Matn qaysi alifboda ko'proq yozilgan bo'lsa
    (kiril/lotin harflar soni bo'yicha), o'sha yo'nalishda aylantiriladi.
    """
    if not query or not query.strip():
        return [query]
    kiril_count = sum(1 for c in query if 'Ѐ' <= c <= 'ӿ')
    lotin_count = sum(1 for c in query if c.isascii() and c.isalpha())
    converted = lotin_to_kiril(query) if lotin_count >= kiril_count else kiril_to_lotin(query)
    variants = [query]
    if converted and converted not in variants:
        variants.append(converted)
    return variants
