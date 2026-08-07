# -*- coding: utf-8 -*-
"""Kirill -> lotin transliteratsiyasi KO'RSATILADIGAN nomlar uchun.

NEGA `utils/transliterate.py` ISHLATILMADI
------------------------------------------
O'sha modul ataylab QIDIRUV uchun yozilgan va o'z docstringida shuni aytadi:
u natijani butunlay kichik harfga tushiradi, ц/щ/ъ/ь/ы harflarini umuman
bilmaydi va "aniq/qaytariladigan transliteratsiya emas". Fan yo'nalishi nomi
saytda sarlavha sifatida ko'rinadi, shuning uchun bosh harf va o'zbek maxsus
harflari to'g'ri chiqishi shart. O'sha modulni o'zgartirish esa butun saytdagi
qidiruvga ta'sir qilardi (h/x ajratilishi qidiruv uchun kritik) — shu sababli
bu yerda ALOHIDA, ko'rsatishga mo'ljallangan funksiya bor.

APOSTROF TANLOVI
----------------
`ў` va `ғ` uchun ASCII apostrof (`'`) ishlatiladi, U+2018 (`o‘`) emas. Sabab:
saytdagi qidiruv (utils/search_helper -> utils/transliterate) so'rovdagi barcha
apostrof variantlarini ASCII `'` ga keltiradi, lekin ustunning O'ZINI
normalizatsiya qilmaydi. Nom `o‘` bilan saqlansa, `o'` bo'yicha qidiruv uni
topa olmaydi. Loyihaning qolgan matnlari ham ASCII apostrof ishlatadi.

BU RASMIY TARJIMA EMAS
----------------------
Natija — determinstik translitеratsiya, rasmiy o'zbek lotin nomi emas. Rasmiy
nom `name_uz_cyrillic` da saqlanadi va u har doim haqiqat manbai. Noaniq
holatlar (ц, щ, ъ, ь, ы, so'z boshidagi е, aralash alifbo) `warnings` ro'yxatida
qaytariladi va validation hisobotiga tushadi.
"""
from __future__ import annotations

import re

APOSTROPHE = "'"

# Bir harfli oddiy moslik. Ko'p harfli (digraf) natijalar ham shu yerda —
# manba tomonda har doim bitta kirill harfi bo'lgani uchun to'qnashuv yo'q.
_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
    'ж': 'j', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k',
    'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p',
    'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
    'х': 'x', 'ч': 'ch', 'ш': 'sh', 'э': 'e',
    'ё': 'yo', 'ю': 'yu', 'я': 'ya',
    # O'zbek kirilining maxsus harflari
    'ў': 'o' + APOSTROPHE,
    'ғ': 'g' + APOSTROPHE,
    'қ': 'q',
    'ҳ': 'h',
    # Noaniq / ruscha meros harflar — natija beriladi, lekin ogohlantiriladi.
    'ц': 's',
    'щ': 'sh',
    'ы': 'i',
    'ъ': APOSTROPHE,
    'ь': '',
}

# Ogohlantirish talab qiladigan harflar (§8) — natija baribir qaytariladi.
_AMBIGUOUS = {
    'ц': 'cyrillic_ts_is_ambiguous',
    'щ': 'cyrillic_shch_is_ambiguous',
    'ы': 'cyrillic_yery_is_ambiguous',
    'ъ': 'cyrillic_hard_sign_is_ambiguous',
    'ь': 'cyrillic_soft_sign_dropped',
    'ё': 'cyrillic_yo_is_ambiguous',
}

_VOWELS = set('аеёиоуўыэюяАЕЁИОУЎЫЭЮЯ')
_CYRILLIC = re.compile(r'[Ѐ-ӿ]')
_LATIN = re.compile(r'[A-Za-z]')


def _is_word_start(text, index):
    """`е` uchun: so'z boshimi yoki unli/ъ/ь dan keyinmi (o'zbek qoidasi)."""
    if index == 0:
        return True
    previous = text[index - 1]
    if previous in _VOWELS or previous in 'ъьЪЬ':
        return True
    return not previous.isalpha()


def _apply_case(source_char, converted):
    """Kirill harf bosh harf bo'lsa natijaning BIRINCHI harfi bosh bo'ladi.

    "Ёнғин" -> "Yong'in" (YOng'in emas): digrafda faqat birinchi harf
    kattalashadi, chunki nom ichida hammasi katta harf bo'lib ketmaydi.
    """
    if not converted or not source_char.isupper():
        return converted
    return converted[0].upper() + converted[1:]


def transliterate_name(text):
    """(latin_text, warnings). Kirill nomni ko'rsatish uchun lotinga o'giradi.

    Kirill bo'lmagan belgilar (raqam, tinish belgisi, lotin harf) o'zgarmaydi.
    """
    if not text:
        return text or '', []

    out = []
    warnings = set()
    for index, char in enumerate(text):
        lower = char.lower()

        if lower == 'е':
            converted = 'ye' if _is_word_start(text, index) else 'e'
            if _is_word_start(text, index):
                warnings.add('cyrillic_ye_at_word_start')
            out.append(_apply_case(char, converted))
            continue

        if lower in _MAP:
            if lower in _AMBIGUOUS:
                warnings.add(_AMBIGUOUS[lower])
            out.append(_apply_case(char, _MAP[lower]))
            continue

        out.append(char)

    latin = ''.join(out)

    # Aralash alifbo: kirill matn ichida lotin harf qolgan bo'lsa (manba
    # xatosi), yoki natija hali ham kirill saqlab qolgan bo'lsa.
    if _CYRILLIC.search(latin):
        warnings.add('unmapped_cyrillic_remains')
    if _LATIN.search(text):
        warnings.add('latin_letters_in_cyrillic_source')

    return latin, sorted(warnings)


def has_cyrillic(text):
    return bool(_CYRILLIC.search(text or ''))


def has_latin(text):
    return bool(_LATIN.search(text or ''))
