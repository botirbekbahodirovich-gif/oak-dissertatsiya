"""Kafedra hujjatlari — har bir hujjat turi uchun forma tuzilmasi + Groq AI promptlari.

Forma endi 2 blokdan iborat (tadqiq.uz tahlili asosida):
  A. Asosiy maydonlar — `main_fields` (majburiy, FIELD_META dan render qilinadi)
  B. Ixtiyoriy — moslashtirish — umumiy bo'limlar (`SECTIONS`) + tur-xos qo'shimcha
     maydonlar (`extra_fields`). Har bir bo'lim standart qiymatlar bilan to'ldirilgan
     keladi — foydalanuvchi faqat o'zgartirmoqchi bo'lsa ochadi.

Umumiy bo'limlar (4 tasi ko'p turda takrorlanadi, shu sabab bir marta yoziladi):
  kredit_soat        — kurs turi, kredit, semestr, o'quv yili, soatlar taqsimoti
  baholash_mezoni     — JN/ON/YN foizlari (yig'indi 100%)
  mavzular_rejasi     — mavzular soni + ro'yxat (yoki mashg'ulotlar_rejasi nomi bilan)
  muassasa_shaxslar   — universitet/kafedra/tuzuvchi/taqrizchi va h.k. (bo'sh qolsa
                        docx_builder sariq placeholder bilan to'ldiradi)

Yangi hujjat turi qo'shish uchun DOCUMENT_TYPES ga bitta yozuv + docx_builder.py da
bitta renderer funksiya kifoya.
"""
import datetime

TALIM_BOSQICHLARI = ['Bakalavriat', 'Magistratura', 'Doktorantura (PhD)']


def current_academic_year():
    """Joriy o'quv yili — sentyabrdan boshlab yangi o'quv yili hisoblanadi."""
    today = datetime.date.today()
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}-{start + 1}"


# ── Umumiy bo'limlar (SECTIONS) — subfield meta: type/label/default/placeholder ─

SECTIONS = {
    'kredit_soat': {
        'title': 'Kredit va soatlar',
        'fields': {
            'kurs_turi': {'type': 'select', 'label': 'Kurs turi', 'options': ['Majburiy', 'Tanlov'], 'default': 'Majburiy'},
            'kredit': {'type': 'number', 'label': 'Kredit (ECTS)', 'default': 3, 'min': 1, 'max': 12},
            'semestr': {'type': 'number', 'label': 'Semestr', 'default': 1, 'min': 1, 'max': 12},
            'oquv_yili': {'type': 'text', 'label': "O'quv yili", 'default_fn': current_academic_year},
            'maruza_soat': {'type': 'number', 'label': 'Ma\'ruza soatlari', 'default': 30, 'min': 0, 'max': 200},
            'amaliy_soat': {'type': 'number', 'label': 'Amaliy soatlar', 'default': 15, 'min': 0, 'max': 200},
            'lab_soat': {'type': 'number', 'label': 'Laboratoriya soatlari', 'default': 0, 'min': 0, 'max': 200},
            'seminar_soat': {'type': 'number', 'label': 'Seminar soatlari', 'default': 0, 'min': 0, 'max': 200},
            'mustaqil_soat': {'type': 'number', 'label': "Mustaqil ta'lim soatlari", 'default': 45, 'min': 0, 'max': 300},
        },
        'hours_fields': ['maruza_soat', 'amaliy_soat', 'lab_soat', 'seminar_soat', 'mustaqil_soat'],
    },
    'baholash_mezoni': {
        'title': 'Baholash mezoni',
        'hint': "JN — joriy, ON — oraliq, YN — yakuniy nazorat. Yig'indi 100% bo'lishi shart (odatda 30/20/50).",
        'chips': [(30, 20, 50), (40, 20, 40), (30, 30, 40)],
        'fields': {
            'jn_foiz': {'type': 'number', 'label': 'Joriy nazorat (%)', 'default': 30, 'min': 0, 'max': 100},
            'on_foiz': {'type': 'number', 'label': 'Oraliq nazorat (%)', 'default': 20, 'min': 0, 'max': 100},
            'yn_foiz': {'type': 'number', 'label': 'Yakuniy nazorat (%)', 'default': 50, 'min': 0, 'max': 100},
        },
        'percent_fields': ['jn_foiz', 'on_foiz', 'yn_foiz'],
    },
    'mavzular_rejasi': {
        'title': 'Mavzular rejasi',
        'hint': "Bo'sh qoldirsangiz, AI mavzularni fan nomi asosida tuzadi. Yoki o'z ro'yxatingizni har bir mavzu yangi qatordan kiriting.",
        'soni_range': (4, 24), 'soni_default': 12,
        'fields': {
            'mavzular_soni': {'type': 'number', 'label': 'Mavzular soni', 'default': 12, 'min': 4, 'max': 24,
                              'placeholder': '4-24'},
            'mavzular_royxati': {'type': 'textarea', 'label': 'Mavzular ro\'yxati',
                                 'placeholder': "Kirish. Fanning maqsad va vazifalari\nAsosiy tushunchalar\n..."},
            'mavzular_qoshimcha': {'type': 'textarea', 'label': "Qo'shimcha ma'lumot (AI uchun)",
                                   'placeholder': "AI ga qo'shimcha kontekst bersangiz shu yerga yozing"},
        },
    },
    'mashgulotlar_rejasi': {
        'title': "Mashg'ulotlar rejasi",
        'hint': "Bo'sh qoldirsangiz, AI mashg'ulot mavzularini fan nomi asosida tuzadi. Yoki o'z ro'yxatingizni har bir mavzu yangi qatordan kiriting.",
        'soni_range': (4, 16), 'soni_default': 10,
        'fields': {
            'mavzular_soni': {'type': 'number', 'label': "Mashg'ulotlar soni", 'default': 10, 'min': 4, 'max': 16,
                              'placeholder': '4-16'},
            'mavzular_royxati': {'type': 'textarea', 'label': "Mashg'ulot mavzulari",
                                 'placeholder': "1-mashg'ulot mavzusi\n2-mashg'ulot mavzusi\n..."},
            'mavzular_qoshimcha': {'type': 'textarea', 'label': "Qo'shimcha ma'lumot (AI uchun)",
                                   'placeholder': "AI ga qo'shimcha kontekst bersangiz shu yerga yozing"},
        },
    },
    'muassasa_shaxslar_full': {
        'title': 'Muassasa va shaxslar',
        'warning': ("Bu maydonlarni bo'sh qoldirishingiz mumkin. Tayyor Word hujjatida universitet, kafedra, "
                    "tuzuvchi F.I.Sh., taqrizchilar, sanalar va boshqa ma'lumotlar o'rnida sariq rangli bo'sh "
                    "joy paydo bo'ladi — ularni keyin o'zingiz to'ldirasiz."),
        'fields': {
            'universitet': {'type': 'text', 'label': 'Universitet nomi', 'full': True},
            'fakultet': {'type': 'text', 'label': 'Fakultet'},
            'kafedra_nomi': {'type': 'text', 'label': 'Kafedra nomi'},
            'rahbar_fio': {'type': 'text', 'label': 'Rektor F.I.Sh.'},  # runtime da label almashtiriladi
            'shahar': {'type': 'text', 'label': 'Shahar', 'default': 'Toshkent'},
            'kafedra_manzil': {'type': 'text', 'label': 'Kafedra manzili (bino, xona)'},
            'fan_kodi': {'type': 'text', 'label': 'Fan kodi'},
            'bilim_sohasi_diplom': {'type': 'text', 'label': 'Bilim sohasi (diplom tasnifi)'},
            'talim_sohasi_diplom': {'type': 'text', 'label': "Ta'lim sohasi (diplom tasnifi)"},
            'talim_yonalishi_diplom': {'type': 'text', 'label': "Ta'lim yo'nalishi (diplom kodi va nomi)", 'full': True},
            'tuzuvchi_fio': {'type': 'text', 'label': "O'qituvchi/Tuzuvchi F.I.Sh.", 'full': True},
            'ilmiy_daraja_unvon': {'type': 'text', 'label': 'Ilmiy daraja/unvon'},
            'lavozim': {'type': 'text', 'label': 'Lavozim'},
            'email': {'type': 'text', 'label': 'E-mail'},
            'telefon': {'type': 'text', 'label': 'Telefon'},
            'taqrizchi1_fio': {'type': 'text', 'label': '1-taqrizchi F.I.Sh.'},
            'taqrizchi1_daraja': {'type': 'text', 'label': "1-taqrizchi daraja va muassasasi"},
            'taqrizchi2_fio': {'type': 'text', 'label': '2-taqrizchi F.I.Sh.'},
            'taqrizchi2_daraja': {'type': 'text', 'label': "2-taqrizchi daraja va muassasasi"},
            'oquv_yili': {'type': 'text', 'label': "O'quv yili", 'default_fn': current_academic_year},
        },
    },
    'muassasa_shaxslar_qisqa': {
        'title': 'Muassasa va shaxslar',
        'warning': ("Bu maydonlarni bo'sh qoldirishingiz mumkin. Tayyor Word hujjatida universitet, kafedra va "
                    "tuzuvchi F.I.Sh. o'rnida sariq rangli bo'sh joy paydo bo'ladi — ularni keyin o'zingiz to'ldirasiz."),
        'fields': {
            'universitet': {'type': 'text', 'label': 'Universitet nomi', 'full': True},
            'kafedra_nomi': {'type': 'text', 'label': 'Kafedra nomi'},
            'tuzuvchi_fio': {'type': 'text', 'label': "Tuzuvchi F.I.Sh."},
            'oquv_yili': {'type': 'text', 'label': "O'quv yili", 'default_fn': current_academic_year},
        },
    },
}

# Sillabus/fan-dasturi — "Rektor F.I.Sh."; ishchi-dastur/uslubiy-qollanma — "Prorektor F.I.Sh."
RAHBAR_LABELS = {
    'sillabus': 'Rektor F.I.Sh.',
    'fan-dasturi': 'Rektor F.I.Sh.',
    'ishchi-dastur': 'Prorektor F.I.Sh.',
    'uslubiy-qollanma': 'Prorektor F.I.Sh.',
}

# ── Har bir hujjat turi ───────────────────────────────────────────────────────

DOCUMENT_TYPES = {
    'sillabus': {
        'label': 'Sillabus', 'icon': '📘', 'title': 'Sillabus yaratish',
        'tagline': "OAK standartida fan sillabusini sun'iy intellekt yordamida yarating",
        'description': "Fan bo'yicha to'liq sillabus: maqsad, natijalar, mavzular rejasi, baholash, adabiyotlar.",
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi'],
        'sections': ['kredit_soat', 'baholash_mezoni', 'mavzular_rejasi', 'muassasa_shaxslar_full'],
        'extra_fields': [],
        'schema_hint': """{
  "fanning_maqsadi": "matn",
  "fanning_vazifalari": ["...", "..."],
  "kutilayotgan_natijalar": [{"kod": "LO1", "matn": "..."}],
  "mavzular_rejasi": [{"hafta": 1, "mavzu": "...", "mazmuni": "...", "maruza_soati": 2, "amaliy_soati": 1}],
  "baholash_mezoni": {"joriy_nazorat": 30, "oraliq_nazorat": 20, "yakuniy_nazorat": 50},
  "adabiyotlar": {"asosiy": ["...", "..."], "qoshimcha": ["...", "..."]}
}""",
    },
    'ishchi-dastur': {
        'label': "Ishchi o'quv dasturi", 'icon': '📗', 'title': "Ishchi o'quv dasturi yaratish",
        'tagline': "OAK standartida fanning ishchi o'quv dasturini sun'iy intellekt yordamida yarating",
        'description': "Kafedra tomonidan tasdiqlanadigan to'liq ishchi o'quv dasturi.",
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi'],
        'sections': ['kredit_soat', 'baholash_mezoni', 'mavzular_rejasi', 'muassasa_shaxslar_full'],
        'extra_fields': [],
        'schema_hint': """{
  "fanning_maqsadi": "matn",
  "fanning_vazifalari": ["...", "..."],
  "boglanish_fanlar": ["oldingi fanlar", "..."],
  "kutilayotgan_natijalar": [{"kod": "LO1", "matn": "..."}],
  "mavzular_rejasi": [{"hafta": 1, "mavzu": "...", "mazmuni": "...", "maruza_soati": 2, "amaliy_soati": 1, "mustaqil_soati": 3}],
  "baholash_mezoni": {"joriy_nazorat": 30, "oraliq_nazorat": 20, "yakuniy_nazorat": 50},
  "adabiyotlar": {"asosiy": ["...", "..."], "qoshimcha": ["...", "..."], "internet_resurslar": ["...", "..."]}
}""",
    },
    'uslubiy-qollanma': {
        'label': "Uslubiy qo'llanma", 'icon': '📙', 'title': "Uslubiy qo'llanma yaratish",
        'tagline': "OAK standartida amaliy, seminar yoki laboratoriya mashg'ulotlari uchun uslubiy qo'llanmani sun'iy intellekt yordamida yarating",
        'description': "Amaliy/seminar/laboratoriya mashg'ulotlari uchun uslubiy qo'llanma.",
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi', 'mashgulot_turi'],
        'extra_fields': [],
        'sections': ['mashgulotlar_rejasi', 'muassasa_shaxslar_full'],
        'schema_hint': """{
  "kirish": "matn — nima uchun bu qo'llanma kerak",
  "umumiy_korsatmalar": ["...", "..."],
  "mashgulotlar": [{"tartib_raqami": 1, "mavzu": "...", "maqsad": "...", "topshiriqlar": ["...", "..."], "nazorat_savollari": ["...", "..."]}],
  "adabiyotlar": {"asosiy": ["...", "..."], "qoshimcha": ["...", "..."]}
}""",
    },
    'fan-dasturi': {
        'label': 'Fan dasturi (namunaviy)', 'icon': '📕', 'title': 'Fan dasturi yaratish',
        'tagline': "OAK standartida namunaviy fan dasturini sun'iy intellekt yordamida yarating",
        'description': 'OAK standartidagi namunaviy fan dasturi — OTMlar uchun namuna hujjat.',
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi'],
        'sections': ['kredit_soat', 'mavzular_rejasi', 'muassasa_shaxslar_full'],
        'extra_fields': [],
        'schema_hint': """{
  "fanning_maqsadi": "matn",
  "fanning_vazifalari": ["...", "..."],
  "boglanish_fanlar": ["...", "..."],
  "kutilayotgan_natijalar": [{"kod": "LO1", "matn": "..."}],
  "mavzular_rejasi": [{"hafta": 1, "mavzu": "...", "mazmuni": "...", "maruza_soati": 2, "amaliy_soati": 1}],
  "adabiyotlar": {"asosiy": ["...", "..."], "qoshimcha": ["...", "..."]}
}""",
    },
    'mustaqil-talim': {
        'label': "Mustaqil ta'lim topshiriqlari", 'icon': '📝', 'title': "Mustaqil ta'lim topshiriqlari yaratish",
        'tagline': "Fan bo'yicha mustaqil ta'lim topshiriqlari to'plamini sun'iy intellekt yordamida yarating",
        'description': "Har bir mavzu bo'yicha mustaqil ish topshirig'i, adabiyotlar va baholash rubrikasi.",
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi', 'topshiriqlar_soni'],
        'extra_fields': ['topshiriq_turlari'],
        'sections': ['mavzular_rejasi', 'muassasa_shaxslar_qisqa'],
        'schema_hint': """{
  "kirish": "matn",
  "topshiriqlar": [{"raqam": 1, "mavzu": "...", "topshiriq_matni": "...", "hajm": "masalan 5-7 bet",
                     "baholash_mezoni": "qisqa mezon", "adabiyotlar": ["...", "..."]}],
  "umumiy_baholash_rubrikasi": [{"mezon": "...", "ball": 5}]
}""",
    },
    'baholash-mezonlari': {
        'label': 'Baholash mezonlari va rubrikalar', 'icon': '📊', 'title': 'Baholash mezonlari yaratish',
        'tagline': "Fan topshiriqlari uchun baholash mezonlari va rubrikalarni sun'iy intellekt yordamida yarating",
        'description': "Har bir topshiriq turi uchun batafsil rubrika va ballar taqsimoti.",
        'main_fields': ['fan_nomi', 'soha', 'talim_bosqichi', 'baholash_turi'],
        'extra_fields': [],
        'sections': ['baholash_mezoni', 'muassasa_shaxslar_qisqa'],
        'schema_hint': """{
  "umumiy_tamoyillar": "matn",
  "baholash_turlari": [{"tur": "...", "maksimal_ball": 100,
                         "mezonlar": [{"mezon": "...", "ball": 20, "tavsif": "..."}]}],
  "baholash_shkalasi": [{"daraja": "a'lo", "ball_oralig'i": "86-100", "tavsif": "..."}]
}""",
    },
    'test-savollari': {
        'label': 'Test savollari banki', 'icon': '✅', 'title': 'Test savollari yaratish',
        'tagline': "Fan bo'yicha test savollari bankini sun'iy intellekt yordamida yarating",
        'description': "Ko'p variantli test savollari (4 variant, to'g'ri javob belgilangan).",
        'main_fields': ['fan_nomi', 'soha', 'talim_bosqichi', 'savollar_soni', 'qiyinlik_darajasi'],
        'extra_fields': ['test_format'],
        'sections': ['mavzular_rejasi', 'muassasa_shaxslar_qisqa'],
        'schema_hint': """{
  "savollar": [{"raqam": 1, "savol": "...", "variantlar": {"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."},
                "togri_javob": "A", "qiyinlik": "o'rtacha"}]
}""",
    },
    'imtihon-savollari': {
        'label': 'Imtihon savollari', 'icon': '🗒️', 'title': 'Imtihon savollari yaratish',
        'tagline': "Oraliq va yakuniy imtihon uchun savollar variantlarini sun'iy intellekt yordamida yarating",
        'description': "Ochiq savollar variantlari va javob namunalari.",
        'main_fields': ['fan_nomi', 'soha', 'talim_bosqichi', 'imtihon_turi', 'variantlar_soni'],
        'extra_fields': ['imtihon_format'],
        'sections': ['mavzular_rejasi', 'muassasa_shaxslar_qisqa'],
        'schema_hint': """{
  "variantlar": [{"variant": 1, "savollar": [{"raqam": 1, "savol": "...", "turi": "nazariy", "javob_namunasi": "qisqa tayanch javob"}]}]
}""",
    },
    'kurs-ishi-mavzulari': {
        'label': 'Kurs ishi mavzulari', 'icon': '🎓', 'title': 'Kurs ishi mavzulari yaratish',
        'tagline': "Kurs ishi va BMI uchun mavzular ro'yxatini sun'iy intellekt yordamida yarating",
        'description': "Kurs ishi/BMI mavzulari ro'yxati, tavsif va adabiyot yo'nalishi bilan.",
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi', 'ish_turi'],
        'extra_fields': ['mavzular_soni', 'kurs_ishi_opsiyalar'],
        'sections': ['muassasa_shaxslar_qisqa'],
        'schema_hint': """{
  "mavzular": [{"raqam": 1, "mavzu": "...", "tavsif": "2-3 gapli qisqa tavsif",
                "tavsiya_adabiyot_yonalishi": "..."}]
}""",
    },
}

VALID_DOC_TYPES = list(DOCUMENT_TYPES.keys())

# ── Asosiy maydonlar (main_fields) meta ──────────────────────────────────────

FIELD_META = {
    'fan_nomi': {'type': 'text', 'label': 'Fan nomi', 'placeholder': 'Masalan: Dasturlash asoslari', 'required': True},
    'soha': {'type': 'text', 'label': 'Soha (OAK)', 'placeholder': "Masalan: 05.00.00 — Texnika fanlari", 'required': True},
    'mutaxassislik': {'type': 'text', 'label': 'Mutaxassislik', 'placeholder': 'Masalan: 05.01.01 — Tizimli tahlil', 'required': True},
    'talim_bosqichi': {'type': 'select', 'label': "Ta'lim bosqichi", 'options': TALIM_BOSQICHLARI, 'required': True},
    'topshiriqlar_soni': {'type': 'number', 'label': 'Topshiriqlar soni', 'default': 15, 'min': 5, 'max': 30, 'required': True},
    'baholash_turi': {'type': 'select', 'label': 'Baholash turi', 'required': True,
                      'options': ["Yozma imtihon", "Og'zaki imtihon", 'Test', 'Amaliy loyiha', 'Taqdimot', 'Barcha turlar']},
    'savollar_soni': {'type': 'chips', 'label': 'Savollar soni', 'options': ['25', '50', '100'], 'default': '25', 'required': True},
    'qiyinlik_darajasi': {'type': 'chips', 'label': 'Qiyinlik darajasi', 'required': True,
                          'options': ['Oson', "O'rtacha", 'Qiyin', 'Aralash'], 'default': 'Aralash'},
    'imtihon_turi': {'type': 'toggle', 'label': 'Imtihon turi', 'options': ['Oraliq nazorat', 'Yakuniy nazorat'],
                     'default': 'Oraliq nazorat', 'required': True},
    'variantlar_soni': {'type': 'number', 'label': 'Variantlar soni', 'default': 25, 'min': 5, 'max': 50, 'required': True},
    'ish_turi': {'type': 'toggle', 'label': 'Ish turi', 'required': True,
                'options': ['Kurs ishi', 'BMI (bitiruv malakaviy ishi)', 'Magistrlik dissertatsiyasi'],
                'default': 'Kurs ishi'},
}

# ── Tur-xos qo'shimcha (extra_fields) — maxsus widgetlar ─────────────────────

EXTRA_FIELDS = {
    'mashgulot_turi': {
        'type': 'toggle', 'label': "Mashg'ulot turi", 'options': ['Amaliy', 'Seminar', 'Laboratoriya'],
        'default': 'Amaliy', 'hint': "Qo'llanma qaysi turdagi mashg'ulotlar uchun ekanligini tanlang.",
    },
    'topshiriq_turlari': {
        'type': 'checkboxes', 'label': 'Topshiriq turlari', 'min_selected': 1,
        'options': ['Referat', 'Taqdimot', 'Esse', 'Loyiha ishi', 'Keys tahlili', 'Portfolio'],
        'default': ['Referat', 'Taqdimot', 'Esse'],
    },
    'test_format': {
        'type': 'group', 'title': 'Format',
        'fields': {
            'variantlar_soni_savolda': {'type': 'chips', 'label': 'Variantlar soni har savolda', 'options': ['4', '5'], 'default': '4'},
            'javoblar_kaliti': {'type': 'checkbox', 'label': "To'g'ri javoblar alohida kalitda ko'rsatilsin",
                                'default': True},
        },
    },
    'imtihon_format': {
        'type': 'group', 'title': 'Format',
        'fields': {
            'savollar_soni_variantda': {'type': 'number', 'label': 'Har variantda savollar soni', 'default': 3, 'min': 1, 'max': 10},
            'savol_turlari': {'type': 'checkboxes', 'label': 'Savol turlari', 'min_selected': 1,
                              'options': ['Nazariy', 'Amaliy/masala', 'Ijodiy'], 'default': ['Nazariy', 'Amaliy/masala']},
        },
    },
    'mavzular_soni': {
        'type': 'number', 'label': 'Mavzular soni', 'default': 30, 'min': 10, 'max': 50,
    },
    'kurs_ishi_opsiyalar': {
        'type': 'group', 'title': None,
        'fields': {
            'tavsif_qoshilsin': {'type': 'checkbox', 'label': 'Har mavzuga qisqacha tavsif qo\'shilsin', 'default': True},
            'adabiyot_yonalishi': {'type': 'checkbox', 'label': "Tavsiya etiladigan adabiyot yo'nalishi ko'rsatilsin", 'default': True},
        },
    },
}


def _flatten(form, keys):
    parts = []
    for k in keys:
        v = form.get(k)
        if isinstance(v, list):
            v = ', '.join(str(x) for x in v)
        if isinstance(v, str):
            v = v.strip()
        if v not in (None, '', []):
            parts.append((k, v))
    return parts


def build_prompt(doc_type, form):
    """form — foydalanuvchi kiritgan qiymatlar dict (flat, section prefiksisiz).
    Qaytaradi: (system_prompt, user_prompt)."""
    cfg = DOCUMENT_TYPES[doc_type]
    lines = [f"Hujjat turi: {cfg['label']}"]

    for key in cfg['main_fields']:
        meta = FIELD_META.get(key) or EXTRA_FIELDS.get(key) or {}
        val = form.get(key)
        if val not in (None, '', []):
            lines.append(f"{meta.get('label', key)}: {val}")

    for key in cfg.get('extra_fields', []):
        meta = EXTRA_FIELDS.get(key, {})
        if meta.get('type') == 'group':
            for fk, fmeta in meta['fields'].items():
                val = form.get(fk)
                if val not in (None, '', [], False):
                    lines.append(f"{fmeta.get('label', fk)}: {val}")
        else:
            val = form.get(key)
            if val not in (None, '', []):
                lines.append(f"{meta.get('label', key)}: {val}")

    for sect_key in cfg.get('sections', []):
        sect = SECTIONS.get(sect_key, {})
        present = _flatten(form, sect.get('fields', {}).keys())
        if not present:
            continue
        lines.append(f"— {sect['title']} —")
        for fk, val in present:
            fmeta = sect['fields'].get(fk, {})
            lines.append(f"{fmeta.get('label', fk)}: {val}")

    user_prompt = (
        "\n".join(lines) +
        "\n\nQuyidagi JSON strukturasida javob ber (faqat JSON, boshqa hech narsa yo'q):\n" +
        cfg['schema_hint']
    )
    return _SYSTEM_PROMPT, user_prompt


_SYSTEM_PROMPT = (
    "Sen O'zbekiston oliy ta'lim tizimi uchun OAK (Oliy attestatsiya komissiyasi) "
    "standartlariga muvofiq rasmiy o'quv-uslubiy hujjatlar tayyorlaydigan mutaxassissan. "
    "Javobing FAQAT so'ralgan JSON strukturasida bo'lishi kerak — hech qanday qo'shimcha "
    "matn, izoh yoki markdown belgilash (```json kabi) qo'shma. Barcha matn o'zbek tilida, "
    "aniq, akademik uslubda va real, foydali mazmunda bo'lsin. Adabiyotlar ro'yxatida "
    "haqiqiy yoki haqiqatga yaqin O'zbekiston va xalqaro nashrlarni tavsiya qil."
)


# "Namuna bilan to'ldirish" tugmasi uchun — mavzular_rejasi/mashgulotlar_rejasi
# textarea'siga bosilganda quyiladigan namunaviy ro'yxat (tur bo'yicha).
SAMPLE_TOPICS = {
    'sillabus': ["Kirish. Fanning maqsad va vazifalari", "Asosiy tushunchalar va atamalar",
                "Nazariy asoslar — 1-qism", "Nazariy asoslar — 2-qism", "Amaliy metodlar",
                "Zamonaviy tendentsiyalar", "Yakunlash va mustahkamlash"],
    'ishchi-dastur': ["Kirish. Fan predmeti va vazifalari", "Asosiy tushunchalar",
                      "Nazariy qism — 1-bo'lim", "Nazariy qism — 2-bo'lim", "Amaliy mashg'ulotlar",
                      "Mustaqil ish topshiriqlari", "Yakuniy takrorlash"],
    'fan-dasturi': ["Kirish", "Asosiy tushunchalar", "Nazariy asoslar", "Amaliy qo'llanilishi",
                    "Zamonaviy yo'nalishlar", "Yakunlash"],
    'mashgulotlar_rejasi': ["1-mashg'ulot: Kirish va asosiy tushunchalar", "2-mashg'ulot: Amaliy usullar",
                            "3-mashg'ulot: Murakkab masalalar", "4-mashg'ulot: Yakuniy nazorat"],
}


def section_defaults(sect_key):
    """Bo'lim uchun standart qiymatlar dict (bosh formani birinchi marta to'ldirish uchun)."""
    sect = SECTIONS.get(sect_key, {})
    out = {}
    for fk, fmeta in sect.get('fields', {}).items():
        if 'default_fn' in fmeta:
            out[fk] = fmeta['default_fn']()
        elif 'default' in fmeta:
            out[fk] = fmeta['default']
    return out


def section_summary(sect_key, values):
    """Yopiq kollaps bo'lim uchun qisqacha xulosa matni (masalan '3 kredit · 90 soat · ...')."""
    if sect_key == 'kredit_soat':
        hours = sum(int(values.get(f) or 0) for f in SECTIONS['kredit_soat']['hours_fields'])
        return (f"{values.get('kredit', 3)} kredit · {hours} soat · "
                f"{values.get('maruza_soat', 0)} ma'ruza · {values.get('amaliy_soat', 0)} amaliy · "
                f"{values.get('mustaqil_soat', 0)} mustaqil")
    if sect_key == 'baholash_mezoni':
        return f"{values.get('jn_foiz', 30)}/{values.get('on_foiz', 20)}/{values.get('yn_foiz', 50)}"
    if sect_key in ('mavzular_rejasi', 'mashgulotlar_rejasi'):
        return f"{values.get('mavzular_soni', SECTIONS[sect_key]['soni_default'])} mavzu"
    if sect_key in ('muassasa_shaxslar_full', 'muassasa_shaxslar_qisqa'):
        uni = (values.get('universitet') or '').strip()
        return uni if uni else "to'ldirilmagan (sariq placeholder bilan)"
    return ''
