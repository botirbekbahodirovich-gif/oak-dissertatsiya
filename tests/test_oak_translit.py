# -*- coding: utf-8 -*-
"""utils/oak_translit.py — ko'rsatiladigan nomlar uchun transliteratsiya.

Bu RASMIY TARJIMA emas, deterministik transliteratsiya. Rasmiy nom har doim
`name_uz_cyrillic` da; bu yerda faqat lotin variantining barqarorligi va
noaniq holatlar uchun ogohlantirish berilishi tekshiriladi.
"""
import unittest

from utils.oak_translit import has_cyrillic, has_latin, transliterate_name


def latin(text):
    return transliterate_name(text)[0]


def warnings(text):
    return transliterate_name(text)[1]


class UzbekLettersTests(unittest.TestCase):
    """O'zbek kirilining maxsus harflari — bular eng ko'p xato qilinadigan joy."""

    def test_o_with_breve_becomes_o_apostrophe(self):
        self.assertEqual(latin('Ўзбек тили'), "O'zbek tili")

    def test_g_with_stroke_becomes_g_apostrophe(self):
        self.assertEqual(latin('Ғоялар'), "G'oyalar")

    def test_qaf_becomes_q(self):
        self.assertEqual(latin('Қорақалпоқ'), 'Qoraqalpoq')

    def test_ha_becomes_h_and_kha_becomes_x(self):
        # h va x ATAYIN ikki xil harf — "ҳимоя"/himoya va "хат"/xat.
        self.assertEqual(latin('Ҳисоблаш'), 'Hisoblash')
        self.assertEqual(latin('Хирургия'), 'Xirurgiya')
        self.assertEqual(latin('Фиқҳ'), 'Fiqh')

    def test_digraphs(self):
        self.assertEqual(latin('Шошилинч'), 'Shoshilinch')
        self.assertEqual(latin('Журналистика'), 'Jurnalistika')

    def test_ascii_apostrophe_is_used_not_a_curly_one(self):
        # Qidiruv (utils/transliterate) so'rovni ASCII apostrofga keltiradi,
        # lekin ustunni normalizatsiya qilmaydi — shuning uchun saqlangan nom
        # ham ASCII bo'lishi shart, aks holda "o'zbek" qidiruvi topa olmaydi.
        result = latin('Ўзбекистон')
        self.assertIn("'", result)
        self.assertNotIn('‘', result)
        self.assertNotIn('ʻ', result)


class IotatedVowelTests(unittest.TestCase):
    def test_yo_yu_ya(self):
        self.assertEqual(latin('Ёнғин'), "Yong'in")
        self.assertEqual(latin('Юрак'), 'Yurak')
        self.assertEqual(latin('Ярим'), 'Yarim')

    def test_ye_at_word_start(self):
        self.assertEqual(latin('Европа'), 'Yevropa')
        self.assertEqual(latin('Ер тузиш'), 'Yer tuzish')

    def test_e_inside_a_word_stays_e(self):
        self.assertEqual(latin('Театр'), 'Teatr')
        self.assertEqual(latin('Менежмент'), 'Menejment')

    def test_word_initial_ye_is_reported_as_a_warning(self):
        self.assertIn('cyrillic_ye_at_word_start', warnings('Европа'))
        self.assertNotIn('cyrillic_ye_at_word_start', warnings('Театр'))


class AmbiguousLetterTests(unittest.TestCase):
    """§8 — noaniq holatlar natija beradi, LEKIN ogohlantiriladi."""

    def test_ts_is_converted_and_warned(self):
        self.assertEqual(latin('Селекция'), 'Seleksiya')
        self.assertIn('cyrillic_ts_is_ambiguous', warnings('Селекция'))

    def test_hard_sign_becomes_an_apostrophe_and_is_warned(self):
        self.assertEqual(latin('сунъий'), "sun'iy")
        self.assertIn('cyrillic_hard_sign_is_ambiguous', warnings('сунъий'))

    def test_soft_sign_is_dropped_and_warned(self):
        self.assertEqual(latin('фильтр'), 'filtr')
        self.assertIn('cyrillic_soft_sign_dropped', warnings('фильтр'))

    def test_shcha_and_yery_are_handled_and_warned(self):
        self.assertEqual(latin('щит'), 'shit')
        self.assertIn('cyrillic_shch_is_ambiguous', warnings('щит'))
        self.assertIn('cyrillic_yery_is_ambiguous', warnings('быт'))

    def test_yo_is_warned_because_conventions_differ(self):
        self.assertIn('cyrillic_yo_is_ambiguous', warnings('жараёнлар'))

    def test_a_clean_name_produces_no_warnings(self):
        self.assertEqual(warnings('Математик анализ'), [])


class CasePreservationTests(unittest.TestCase):
    def test_capital_stays_capital(self):
        self.assertTrue(latin('Оптика')[0].isupper())

    def test_only_the_first_letter_of_a_digraph_is_capitalised(self):
        self.assertEqual(latin('Ёнғин'), "Yong'in")
        self.assertNotEqual(latin('Ёнғин'), "YOng'in")

    def test_lowercase_input_stays_lowercase(self):
        self.assertEqual(latin('оптика'), 'optika')

    def test_a_lowercase_source_name_is_not_silently_capitalised(self):
        # Manbadagi bosh harf xatosi TUZATILMAYDI — u xato belgisi bo'lishi
        # mumkin va normalizatsiya qoidalari ro'yxatida yo'q.
        self.assertEqual(latin('экспериментал физика'), 'eksperimental fizika')


class MixedAndEdgeCaseTests(unittest.TestCase):
    def test_punctuation_digits_and_spacing_survive(self):
        self.assertEqual(latin('05.01.05 — Ахборот'), '05.01.05 — Axborot')

    def test_hyphenated_names(self):
        self.assertEqual(latin('Юрак-қон томир'), 'Yurak-qon tomir')

    def test_latin_letters_in_a_cyrillic_source_are_flagged(self):
        self.assertIn('latin_letters_in_cyrillic_source',
                      warnings("Хизмат ko'рсатиш"))

    def test_empty_input_is_safe(self):
        self.assertEqual(transliterate_name(''), ('', []))
        self.assertEqual(transliterate_name(None), ('', []))

    def test_result_is_never_empty_for_a_non_empty_cyrillic_name(self):
        for name in ('Алгебра', 'Оптика', 'Ботаника', 'Экология', 'Ихтиология',
                     'Тупроқшунослик', 'Ўсимликшунослик', 'Қуръоншунослик'):
            self.assertTrue(latin(name).strip(), name)

    def test_no_cyrillic_remains_in_the_output(self):
        for name in ('Ижтимоий онг ва ижтимоий жараёнлар социологияси',
                     'Фавқулодда ҳолатларда хавфсизлик',
                     'Қишлоқ хўжалиги маҳсулотларини сақлаш'):
            self.assertFalse(has_cyrillic(latin(name)), name)

    def test_transliteration_is_deterministic(self):
        name = 'Ахборотларни ҳимоялаш усуллари ва тизимлари'
        self.assertEqual(latin(name), latin(name))

    def test_helper_predicates(self):
        self.assertTrue(has_cyrillic('Оптика'))
        self.assertFalse(has_cyrillic('Optika'))
        self.assertTrue(has_latin('Optika'))
        self.assertFalse(has_latin('Оптика'))


class RealSourceSampleTests(unittest.TestCase):
    """Manbadagi haqiqiy nomlarda kutilgan natija."""

    CASES = [
        ('Математик анализ', 'Matematik analiz'),
        ('Дифференциал тенгламалар ва математик физика',
         'Differensial tenglamalar va matematik fizika'),
        ('Ҳисоблаш математикаси ва дискрет математика',
         'Hisoblash matematikasi va diskret matematika'),
        ('Рақамли технологиялар ва сунъий интеллект',
         "Raqamli texnologiyalar va sun'iy intellekt"),
        ('Ўсимликларни ҳимоя қилиш', "O'simliklarni himoya qilish"),
        ('Амалий ва компютер лингвистикаси', 'Amaliy va kompyuter lingvistikasi'),
        ('Мумтоз шарқ адабиёти ва манбашунослиги',
         'Mumtoz sharq adabiyoti va manbashunosligi'),
    ]

    def test_expected_output(self):
        for cyrillic, expected in self.CASES:
            self.assertEqual(latin(cyrillic), expected, cyrillic)


if __name__ == '__main__':
    unittest.main()
