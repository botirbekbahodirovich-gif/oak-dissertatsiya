"""Kafedra hujjatlari — har bir hujjat turi uchun Groq AI prompt shablonlari.

Har bir yozuv: label/icon/description (UI uchun), asosiy va ixtiyoriy maydonlar
ro'yxati (forma qurish uchun, `kafedra/form_base.html` shu ro'yxatdan render
qiladi) va `schema_hint` — AI dan qaytishi kerak bo'lgan JSON strukturasi matn
ko'rinishida (chat modelga ko'rsatiladi, `document_generator.py` shu bo'yicha
javobni tekshiradi/parse qiladi).

Yangi hujjat turi qo'shish uchun shu faylga bitta yozuv qo'shish kifoya —
routes/templates umumiy (generic) infratuzilmadan foydalanadi.
"""

TALIM_BOSQICHLARI = ['Bakalavriat', 'Magistratura', 'Doktorantura (PhD)']

DOCUMENT_TYPES = {
    'sillabus': {
        'label': 'Sillabus',
        'icon': '📘',
        'description': "Fan bo'yicha to'liq sillabus: maqsad, natijalar, mavzular rejasi, baholash, adabiyotlar.",
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi'],
        'optional_fields': ['kredit_soat', 'baholash_ulushi', 'muassasa_shaxslar'],
        'schema_hint': """{
  "fanning_maqsadi": "matn",
  "fanning_vazifalari": ["...", "..."],
  "kutilayotgan_natijalar": [{"kod": "LO1", "matn": "..."}],
  "mavzular_rejasi": [{"hafta": 1, "mavzu": "...", "mazmuni": "...", "maruza_soati": 2, "amaliy_soati": 1}],
  "baholash_mezoni": {"oraliq_nazorat": 30, "mustaqil_ish": 20, "yakuniy_nazorat": 50},
  "adabiyotlar": {"asosiy": ["...", "..."], "qoshimcha": ["...", "..."]}
}""",
    },
    'ishchi-dastur': {
        'label': "Ishchi o'quv dasturi",
        'icon': '📗',
        'description': "Kafedra tomonidan tasdiqlanadigan to'liq ishchi o'quv dasturi.",
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi'],
        'optional_fields': ['kredit_soat', 'baholash_ulushi', 'muassasa_shaxslar'],
        'schema_hint': """{
  "fanning_maqsadi": "matn",
  "fanning_vazifalari": ["...", "..."],
  "boglanish_fanlar": ["oldingi fanlar", "..."],
  "kutilayotgan_natijalar": [{"kod": "LO1", "matn": "..."}],
  "mavzular_rejasi": [{"hafta": 1, "mavzu": "...", "mazmuni": "...", "maruza_soati": 2, "amaliy_soati": 1, "mustaqil_soati": 3}],
  "baholash_mezoni": {"oraliq_nazorat": 30, "mustaqil_ish": 20, "yakuniy_nazorat": 50},
  "adabiyotlar": {"asosiy": ["...", "..."], "qoshimcha": ["...", "..."], "internet_resurslar": ["...", "..."]}
}""",
    },
    'uslubiy-qollanma': {
        'label': "Uslubiy qo'llanma",
        'icon': '📙',
        'description': "Amaliy/seminar/laboratoriya mashg'ulotlari uchun uslubiy qo'llanma.",
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi', 'mashgulot_turi'],
        'optional_fields': ['mashgulotlar_rejasi', 'muassasa_shaxslar'],
        'schema_hint': """{
  "kirish": "matn — nima uchun bu qo'llanma kerak",
  "umumiy_korsatmalar": ["...", "..."],
  "mashgulotlar": [{"tartib_raqami": 1, "mavzu": "...", "maqsad": "...", "topshiriqlar": ["...", "..."], "nazorat_savollari": ["...", "..."]}],
  "adabiyotlar": {"asosiy": ["...", "..."], "qoshimcha": ["...", "..."]}
}""",
    },
    'fan-dasturi': {
        'label': 'Fan dasturi (namunaviy)',
        'icon': '📕',
        'description': 'OAK standartidagi namunaviy fan dasturi — OTMlar uchun namuna hujjat.',
        'main_fields': ['fan_nomi', 'soha', 'mutaxassislik', 'talim_bosqichi'],
        'optional_fields': ['kredit_soat', 'muassasa_shaxslar'],
        'schema_hint': """{
  "fanning_maqsadi": "matn",
  "fanning_vazifalari": ["...", "..."],
  "boglanish_fanlar": ["...", "..."],
  "kutilayotgan_natijalar": [{"kod": "LO1", "matn": "..."}],
  "mavzular_rejasi": [{"hafta": 1, "mavzu": "...", "mazmuni": "...", "maruza_soati": 2, "amaliy_soati": 1}],
  "baholash_mezoni": {"oraliq_nazorat": 30, "mustaqil_ish": 20, "yakuniy_nazorat": 50},
  "adabiyotlar": {"asosiy": ["...", "..."], "qoshimcha": ["...", "..."]}
}""",
    },
    'mustaqil-talim': {
        'label': "Mustaqil ta'lim topshiriqlari",
        'icon': '📝',
        'description': "Har bir mavzu bo'yicha mustaqil ish topshirig'i, adabiyotlar va baholash rubrikasi.",
        'main_fields': ['fan_nomi', 'soha', 'talim_bosqichi', 'topshiriqlar_soni'],
        'optional_fields': ['muassasa_shaxslar'],
        'schema_hint': """{
  "kirish": "matn",
  "topshiriqlar": [{"raqam": 1, "mavzu": "...", "topshiriq_matni": "...", "hajm": "masalan 5-7 bet",
                     "baholash_mezoni": "qisqa mezon", "adabiyotlar": ["...", "..."]}],
  "umumiy_baholash_rubrikasi": [{"mezon": "...", "ball": 5}]
}""",
    },
    'baholash-mezonlari': {
        'label': 'Baholash mezonlari va rubrikalar',
        'icon': '📊',
        'description': "Har bir topshiriq turi uchun batafsil rubrika va ballar taqsimoti.",
        'main_fields': ['fan_nomi', 'baholash_turi'],
        'optional_fields': ['muassasa_shaxslar'],
        'schema_hint': """{
  "umumiy_tamoyillar": "matn",
  "baholash_turlari": [{"tur": "...", "maksimal_ball": 100,
                         "mezonlar": [{"mezon": "...", "ball": 20, "tavsif": "..."}]}],
  "baholash_shkalasi": [{"daraja": "a'lo", "ball_oralig'i": "86-100", "tavsif": "..."}]
}""",
    },
    'test-savollari': {
        'label': 'Test savollari banki',
        'icon': '✅',
        'description': "Ko'p variantli test savollari (4 variant, to'g'ri javob belgilangan).",
        'main_fields': ['fan_nomi', 'soha', 'mavzu', 'savollar_soni', 'qiyinlik_darajasi'],
        'optional_fields': ['muassasa_shaxslar'],
        'schema_hint': """{
  "savollar": [{"raqam": 1, "savol": "...", "variantlar": {"A": "...", "B": "...", "C": "...", "D": "..."},
                "togri_javob": "A", "qiyinlik": "o'rtacha"}]
}""",
    },
    'imtihon-savollari': {
        'label': 'Imtihon savollari',
        'icon': '🗒️',
        'description': "Ochiq savollar variantlari va javob namunalari.",
        'main_fields': ['fan_nomi', 'imtihon_turi', 'variantlar_soni', 'savollar_soni'],
        'optional_fields': ['muassasa_shaxslar'],
        'schema_hint': """{
  "variantlar": [{"variant": 1, "savollar": [{"raqam": 1, "savol": "...", "javob_namunasi": "qisqa tayanch javob"}]}]
}""",
    },
    'kurs-ishi-mavzulari': {
        'label': 'Kurs ishi mavzulari',
        'icon': '🎓',
        'description': "Kurs ishi/BMI mavzulari ro'yxati, tavsif va adabiyot yo'nalishi bilan.",
        'main_fields': ['fan_nomi', 'soha', 'talim_bosqichi', 'mavzular_soni'],
        'optional_fields': ['muassasa_shaxslar'],
        'schema_hint': """{
  "mavzular": [{"raqam": 1, "mavzu": "...", "tavsif": "2-3 gapli qisqa tavsif",
                "tavsiya_adabiyot_yonalishi": "..."}]
}""",
    },
}

VALID_DOC_TYPES = list(DOCUMENT_TYPES.keys())

_SYSTEM_PROMPT = (
    "Sen O'zbekiston oliy ta'lim tizimi uchun OAK (Oliy attestatsiya komissiyasi) "
    "standartlariga muvofiq rasmiy o'quv-uslubiy hujjatlar tayyorlaydigan mutaxassissan. "
    "Javobing FAQAT so'ralgan JSON strukturasida bo'lishi kerak — hech qanday qo'shimcha "
    "matn, izoh yoki markdown belgilash (```json kabi) qo'shma. Barcha matn o'zbek tilida, "
    "aniq, akademik uslubda va real, foydali mazmunda bo'lsin. Adabiyotlar ro'yxatida "
    "haqiqiy yoki haqiqatga yaqin O'zbekiston va xalqaro nashrlarni tavsiya qil."
)


def build_prompt(doc_type, form):
    """form — foydalanuvchi kiritgan qiymatlar dict (kalitlar erkin, faqat bor bo'lganlari
    qo'shiladi). Qaytaradi: (system_prompt, user_prompt)."""
    cfg = DOCUMENT_TYPES[doc_type]
    lines = [f"Hujjat turi: {cfg['label']}"]
    for key in cfg['main_fields'] + cfg['optional_fields']:
        val = (form.get(key) or '').strip() if isinstance(form.get(key), str) else form.get(key)
        if val:
            lines.append(f"{FIELD_LABELS.get(key, key)}: {val}")
    user_prompt = (
        "\n".join(lines) +
        "\n\nQuyidagi JSON strukturasida javob ber (faqat JSON, boshqa hech narsa yo'q):\n" +
        cfg['schema_hint']
    )
    return _SYSTEM_PROMPT, user_prompt


# Forma maydonlarini generik render qilish uchun (kafedra/_form_shared.html).
# type: text | textarea | select | number
FIELD_META = {
    'fan_nomi': {'type': 'text', 'placeholder': 'Masalan: Dasturlash asoslari', 'required': True},
    'soha': {'type': 'text', 'placeholder': "Masalan: 05.00.00 — Texnika fanlari"},
    'mutaxassislik': {'type': 'text', 'placeholder': 'Masalan: 05.01.01 — Tizimli tahlil'},
    'talim_bosqichi': {'type': 'select', 'options': TALIM_BOSQICHLARI},
    'kredit_soat': {'type': 'text', 'placeholder': "3 kredit / 90 soat / 30 ma'ruza / 15 amaliy / 45 mustaqil",
                    'default': "3 kredit / 90 soat / 30 ma'ruza / 15 amaliy / 45 mustaqil"},
    'baholash_ulushi': {'type': 'text', 'placeholder': 'Oraliq/Mustaqil/Yakuniy — masalan 30/20/50',
                        'default': '30/20/50'},
    'muassasa_shaxslar': {'type': 'textarea',
                          'placeholder': "Muassasa nomi, kafedra, tuzuvchi F.I.Sh. va lavozimi"},
    'mashgulot_turi': {'type': 'select', 'options': ['Amaliy', 'Seminar', 'Laboratoriya']},
    'mashgulotlar_rejasi': {'type': 'textarea', 'placeholder': 'Ixtiyoriy — mashg\'ulotlar rejasini qisqacha yozing'},
    'topshiriqlar_soni': {'type': 'number', 'default': 10, 'min': 1, 'max': 30},
    'baholash_turi': {'type': 'select', 'options': ['Yozma imtihon', "Og'zaki imtihon", 'Test',
                                                     'Amaliy loyiha', 'Taqdimot']},
    'mavzu': {'type': 'text', 'placeholder': "Bo'sh qoldirsangiz — barcha mavzular bo'yicha"},
    'savollar_soni': {'type': 'select', 'options': ['25', '50', '100'], 'default': '25'},
    'qiyinlik_darajasi': {'type': 'select', 'options': ['Oson', "O'rtacha", 'Qiyin', 'Aralash'],
                          'default': 'Aralash'},
    'imtihon_turi': {'type': 'select', 'options': ['Oraliq', 'Yakuniy']},
    'variantlar_soni': {'type': 'number', 'default': 4, 'min': 1, 'max': 20},
    'mavzular_soni': {'type': 'number', 'default': 25, 'min': 5, 'max': 60},
}


FIELD_LABELS = {
    'fan_nomi': 'Fan nomi',
    'soha': 'Bilim sohasi (OAK)',
    'mutaxassislik': 'Mutaxassislik',
    'talim_bosqichi': "Ta'lim bosqichi",
    'kredit_soat': 'Kredit va soatlar',
    'baholash_ulushi': 'Baholash mezoni (%)',
    'muassasa_shaxslar': 'Muassasa va mas\'ul shaxslar',
    'mashgulot_turi': "Mashg'ulot turi",
    'mashgulotlar_rejasi': "Mashg'ulotlar rejasi",
    'topshiriqlar_soni': 'Topshiriqlar soni',
    'baholash_turi': 'Baholash turi',
    'mavzu': 'Mavzu',
    'savollar_soni': 'Savollar soni',
    'qiyinlik_darajasi': 'Qiyinlik darajasi',
    'imtihon_turi': 'Imtihon turi',
    'variantlar_soni': 'Variantlar soni',
    'mavzular_soni': 'Mavzular soni',
}
