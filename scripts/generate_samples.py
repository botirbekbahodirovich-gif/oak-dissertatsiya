"""Bir martalik skript: /kafedra bo'limi uchun 9 ta namuna DOCX + mos HTML preview
fragmentlarini generatsiya qiladi ("Namuna ko'rish" modal shu HTML'larni ko'rsatadi,
"Yuklab olish" esa shu DOCX fayllarni).

Ishlatish: python scripts/generate_samples.py   (repo root'dan)

Chiqishlar:
  static/samples/namuna_<doc_type>.docx        — docx_builder.py orqali, to'liq
                                                  to'ldirilgan (sariq placeholder yo'q)
  templates/kafedra/samples/<doc_type>.html    — modal preview uchun statik HTML
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.docx_builder import build_docx, save_docx

SAMPLES_DOCX_DIR = 'static/samples'
SAMPLES_HTML_DIR = 'templates/kafedra/samples'

TATU_META_FULL = {
    'universitet': "Toshkent axborot texnologiyalari universiteti",
    'fakultet': 'Kompyuter injiniringi',
    'kafedra_nomi': 'Dasturiy injiniring',
    'rahbar_fio': "Sh.T. Ganiyev",
    'shahar': 'Toshkent',
    'kafedra_manzil': "2-bino, 314-xona",
    'tuzuvchi_fio': "D.A. Yusupova",
    'ilmiy_daraja_unvon': "PhD, dotsent",
    'lavozim': 'Kafedra dotsenti',
    'email': "d.yusupova@tuit.uz",
    'telefon': "+998 71 238 60 03",
    'taqrizchi1_fio': "B.K. Rahimov",
    'taqrizchi1_daraja': "PhD, TATU dotsenti",
    'taqrizchi2_fio': "N.O. Islomova",
    'taqrizchi2_daraja': "DSc, professor, TATU",
    'oquv_yili': '2025-2026',
}
TATU_META_QISQA = {
    'universitet': "Toshkent axborot texnologiyalari universiteti",
    'kafedra_nomi': 'Dasturiy injiniring',
    'tuzuvchi_fio': "D.A. Yusupova",
    'oquv_yili': '2025-2026',
}

SAMPLES = {}

# 1. Sillabus
SAMPLES['sillabus'] = (
    {**TATU_META_FULL, 'fan_nomi': "Ma'lumotlar tuzilmalari va algoritmlar",
     'soha': '05.00.00 — Texnika fanlari', 'mutaxassislik': '05.01.01 — Tizimli tahlil',
     'talim_bosqichi': 'Bakalavriat', 'rahbar_fio': "T.A. Nazarov",
     'bilim_sohasi_diplom': '5-Muhandislik', 'talim_sohasi_diplom': '5330-Kompyuter injiniringi',
     'talim_yonalishi_diplom': '5330100-Dasturiy injiniring', 'fan_kodi': 'CS-204',
     'kredit': 3, 'semestr': 3, 'maruza_soat': 30, 'amaliy_soat': 15, 'mustaqil_soat': 45},
    {
        "fanning_maqsadi": ("Talabalarda ma'lumotlar tuzilmalari va algoritmlarni loyihalash, tahlil qilish "
                           "va samarali dasturiy yechimlar yaratish ko'nikmalarini shakllantirish."),
        "fanning_vazifalari": [
            "Asosiy chiziqli va nochiziqli ma'lumotlar tuzilmalarini o'rgatish",
            "Algoritmlarning vaqt va xotira murakkabligini baholash usullarini singdirish",
            "Amaliy masalalarni samarali tuzilmalar orqali yechish ko'nikmasini rivojlantirish",
        ],
        "kutilayotgan_natijalar": [
            {"kod": "LO1", "matn": "Asosiy ma'lumotlar tuzilmalarini tanlab qo'llay oladi"},
            {"kod": "LO2", "matn": "Algoritm murakkabligini Big-O yordamida baholaydi"},
            {"kod": "LO3", "matn": "Daraxt va graf algoritmlarini amalga oshiradi"},
        ],
        "mavzular_rejasi": [
            {"hafta": 1, "mavzu": "Kirish. Murakkablik nazariyasi", "mazmuni": "Big-O, Big-Theta tushunchalari", "maruza_soati": 2, "amaliy_soati": 1},
            {"hafta": 2, "mavzu": "Massivlar va bog'langan ro'yxatlar", "mazmuni": "Statik va dinamik tuzilmalar", "maruza_soati": 2, "amaliy_soati": 1},
            {"hafta": 3, "mavzu": "Steklar va navbatlar", "mazmuni": "LIFO/FIFO amaliyotlari", "maruza_soati": 2, "amaliy_soati": 1},
            {"hafta": 4, "mavzu": "Daraxt tuzilmalari", "mazmuni": "Ikkilik qidiruv daraxtlari, AVL", "maruza_soati": 2, "amaliy_soati": 1},
            {"hafta": 5, "mavzu": "Graflar va algoritmlari", "mazmuni": "BFS, DFS, eng qisqa yo'l", "maruza_soati": 2, "amaliy_soati": 1},
        ],
        "baholash_mezoni": {"joriy_nazorat": 30, "oraliq_nazorat": 20, "yakuniy_nazorat": 50},
        "adabiyotlar": {
            "asosiy": ["T. Cormen va boshq. Introduction to Algorithms, 4-nashr, MIT Press, 2022",
                      "R. Sedgewick, K. Wayne. Algorithms, 4-nashr, Addison-Wesley, 2011"],
            "qoshimcha": ["S.S. Skiena. The Algorithm Design Manual, Springer, 2020"],
        },
    },
)

# 2. Ishchi o'quv dasturi
SAMPLES['ishchi-dastur'] = (
    {**TATU_META_FULL, 'fan_nomi': "Sun'iy intellekt asoslari",
     'soha': '05.00.00 — Texnika fanlari', 'mutaxassislik': '05.01.07 — Axborot tizimlari',
     'talim_bosqichi': 'Bakalavriat', 'rahbar_fio': "M.K. Ismoilov",
     'bilim_sohasi_diplom': '5-Muhandislik', 'talim_sohasi_diplom': '5330-Kompyuter injiniringi',
     'talim_yonalishi_diplom': '5330500-Axborot xavfsizligi', 'fan_kodi': 'AI-301',
     'kredit': 4, 'semestr': 5, 'maruza_soat': 30, 'amaliy_soat': 30, 'mustaqil_soat': 60},
    {
        "fanning_maqsadi": "Talabalarni sun'iy intellekt tizimlarini loyihalash va qo'llash asoslari bilan tanishtirish.",
        "fanning_vazifalari": ["Mashinali o'qitish algoritmlarini o'rgatish", "Neyron tarmoqlar asoslarini singdirish"],
        "boglanish_fanlar": ["Ehtimollar nazariyasi va statistika", "Dasturlash asoslari"],
        "kutilayotgan_natijalar": [{"kod": "LO1", "matn": "Mashinali o'qitish modellarini quradi va baholaydi"}],
        "mavzular_rejasi": [
            {"hafta": 1, "mavzu": "SI ga kirish", "mazmuni": "Tarixi va zamonaviy yo'nalishlari", "maruza_soati": 2, "amaliy_soati": 2, "mustaqil_soati": 4},
            {"hafta": 2, "mavzu": "Nazorat qilinadigan o'qitish", "mazmuni": "Regressiya va klassifikatsiya", "maruza_soati": 2, "amaliy_soati": 2, "mustaqil_soati": 4},
            {"hafta": 3, "mavzu": "Neyron tarmoqlar", "mazmuni": "Perseptron, ko'p qatlamli tarmoqlar", "maruza_soati": 2, "amaliy_soati": 2, "mustaqil_soati": 4},
        ],
        "baholash_mezoni": {"joriy_nazorat": 30, "oraliq_nazorat": 20, "yakuniy_nazorat": 50},
        "adabiyotlar": {"asosiy": ["I. Goodfellow va boshq. Deep Learning, MIT Press, 2016"],
                        "qoshimcha": ["S. Russell, P. Norvig. AI: A Modern Approach, 4-nashr, 2020"],
                        "internet_resurslar": ["https://www.coursera.org/learn/machine-learning"]},
    },
)

# 3. Uslubiy qo'llanma
SAMPLES['uslubiy-qollanma'] = (
    {**TATU_META_FULL, 'fan_nomi': 'Analitik kimyo', 'soha': '02.00.00 — Kimyo fanlari',
     'mutaxassislik': '02.00.02 — Analitik kimyo', 'talim_bosqichi': 'Bakalavriat',
     'rahbar_fio': "G.R. Tosheva", 'kafedra_nomi': 'Kimyo texnologiyasi', 'oquv_yili': '2025-2026'},
    {
        "kirish": "Ushbu uslubiy qo'llanma analitik kimyo fanidan amaliy mashg'ulotlarni o'tkazish tartibini belgilaydi.",
        "umumiy_korsatmalar": ["Laboratoriya xavfsizlik qoidalariga qat'iy rioya qilinsin",
                              "Har bir mashg'ulotdan so'ng hisobot topshirilsin"],
        "mashgulotlar": [
            {"tartib_raqami": 1, "mavzu": "Titrlash usullari", "maqsad": "Kislota-asos titrlashni o'zlashtirish",
             "topshiriqlar": ["Eritma tayyorlash", "Titrlash o'tkazish", "Natijalarni hisoblash"],
             "nazorat_savollari": ["Titrlashning mohiyati nima?", "Ekvivalent nuqta qanday aniqlanadi?"]},
            {"tartib_raqami": 2, "mavzu": "Gravimetrik tahlil", "maqsad": "Cho'ktirish reaksiyalarini o'rganish",
             "topshiriqlar": ["Cho'kma hosil qilish", "Filtrlash va quritish", "Massani hisoblash"],
             "nazorat_savollari": ["Gravimetriyaning afzalliklari nima?"]},
        ],
        "adabiyotlar": {"asosiy": ["D.C. Harris. Quantitative Chemical Analysis, 10-nashr, 2020"], "qoshimcha": []},
    },
)

# 4. Fan dasturi
SAMPLES['fan-dasturi'] = (
    {**TATU_META_FULL, 'fan_nomi': 'Dasturlash asoslari', 'soha': '05.00.00 — Texnika fanlari',
     'mutaxassislik': '05.01.01 — Tizimli tahlil', 'talim_bosqichi': 'Bakalavriat',
     'rahbar_fio': "Sh.T. Ganiyev", 'fan_kodi': 'CS-101', 'kredit': 4, 'maruza_soat': 30, 'amaliy_soat': 30},
    {
        "fanning_maqsadi": "Talabalarga zamonaviy dasturlash tillari va algoritmik fikrlash asoslarini o'rgatish.",
        "fanning_vazifalari": ["Sintaksis va semantikani o'rgatish", "Algoritmik fikrlashni rivojlantirish"],
        "boglanish_fanlar": ["Matematik mantiq"],
        "kutilayotgan_natijalar": [{"kod": "LO1", "matn": "Mustaqil dasturiy loyiha yaratadi"}],
        "mavzular_rejasi": [
            {"hafta": 1, "mavzu": "Kirish. O'zgaruvchilar", "mazmuni": "Ma'lumot turlari", "maruza_soati": 2, "amaliy_soati": 2},
            {"hafta": 2, "mavzu": "Shart va sikl operatorlari", "mazmuni": "if/else, for/while", "maruza_soati": 2, "amaliy_soati": 2},
            {"hafta": 3, "mavzu": "Funksiyalar", "mazmuni": "Parametrlar, qaytish qiymati", "maruza_soati": 2, "amaliy_soati": 2},
        ],
        "adabiyotlar": {"asosiy": ["P. Deitel, H. Deitel. Python for Programmers, 2021"], "qoshimcha": []},
    },
)

# 5. Mustaqil ta'lim
SAMPLES['mustaqil-talim'] = (
    {**TATU_META_QISQA, 'fan_nomi': "Ma'lumotlar bazasi", 'soha': '05.00.00 — Texnika fanlari',
     'mutaxassislik': '05.01.07 — Axborot tizimlari', 'talim_bosqichi': 'Bakalavriat'},
    {
        "kirish": "Mustaqil ta'lim topshiriqlari talabaning fan bo'yicha bilimlarini mustahkamlashga xizmat qiladi.",
        "topshiriqlar": [
            {"raqam": 1, "mavzu": "SQL so'rovlari", "topshiriq_matni": "Berilgan ma'lumotlar bazasi asosida 10 ta murakkab SQL so'rov tuzing.",
             "hajm": "5-7 bet", "baholash_mezoni": "To'g'ri sintaksis va natija", "adabiyotlar": ["C.J. Date. SQL and Relational Theory, 2015"]},
            {"raqam": 2, "mavzu": "Normalizatsiya", "topshiriq_matni": "Berilgan jadvalni 3NF holatigacha normallashtiring.",
             "hajm": "4-6 bet", "baholash_mezoni": "Normal shakl talablariga muvofiqlik", "adabiyotlar": []},
        ],
        "umumiy_baholash_rubrikasi": [{"mezon": "Mazmun to'liqligi", "ball": 5}, {"mezon": "Amaliy bajarilishi", "ball": 5}],
    },
)

# 6. Baholash mezonlari
SAMPLES['baholash-mezonlari'] = (
    {**TATU_META_QISQA, 'fan_nomi': 'Web dasturlash', 'soha': '05.00.00 — Texnika fanlari',
     'talim_bosqichi': 'Bakalavriat', 'baholash_turi': 'Amaliy loyiha'},
    {
        "umumiy_tamoyillar": "Baholash mezonlari fanning amaliy yo'nalishini hisobga olgan holda tuzilgan.",
        "baholash_turlari": [
            {"tur": "Amaliy loyiha", "maksimal_ball": 100, "mezonlar": [
                {"mezon": "Funksionallik", "ball": 40, "tavsif": "Loyihaning to'liq ishlashi"},
                {"mezon": "Kod sifati", "ball": 30, "tavsif": "Toza va tushunarli kod"},
                {"mezon": "Taqdimot", "ball": 30, "tavsif": "Loyihani himoya qilish"},
            ]},
        ],
        "baholash_shkalasi": [{"daraja": "a'lo", "ball_oralig'i": "86-100", "tavsif": "Yuqori sifatli bajarilgan"},
                              {"daraja": "yaxshi", "ball_oralig'i": "71-85", "tavsif": "Kamchiliklar minimal"}],
    },
)

# 7. Test savollari
SAMPLES['test-savollari'] = (
    {**TATU_META_QISQA, 'fan_nomi': 'Kompyuter tarmoqlari', 'soha': '05.00.00 — Texnika fanlari',
     'talim_bosqichi': 'Bakalavriat', 'savollar_soni': '25', 'qiyinlik_darajasi': 'Aralash'},
    {
        "savollar": [
            {"raqam": 1, "savol": "OSI modelida nechta qatlam mavjud?",
             "variantlar": {"A": "5", "B": "6", "C": "7", "D": "8"}, "togri_javob": "C", "qiyinlik": "oson"},
            {"raqam": 2, "savol": "TCP protokoli qaysi qatlamda ishlaydi?",
             "variantlar": {"A": "Tarmoq", "B": "Transport", "C": "Ilova", "D": "Fizik"}, "togri_javob": "B", "qiyinlik": "o'rtacha"},
            {"raqam": 3, "savol": "IPv4 manzili nechta bitdan iborat?",
             "variantlar": {"A": "16", "B": "32", "C": "64", "D": "128"}, "togri_javob": "B", "qiyinlik": "oson"},
        ],
    },
)

# 8. Imtihon savollari
SAMPLES['imtihon-savollari'] = (
    {**TATU_META_QISQA, 'fan_nomi': 'Operatsion tizimlar', 'soha': '05.00.00 — Texnika fanlari',
     'talim_bosqichi': 'Bakalavriat', 'imtihon_turi': 'Yakuniy nazorat', 'variantlar_soni': 25},
    {
        "variantlar": [
            {"variant": 1, "savollar": [
                {"raqam": 1, "savol": "Jarayon va oqim (thread) farqini tushuntiring.", "turi": "nazariy",
                 "javob_namunasi": "Jarayon mustaqil xotira maydoniga ega, oqim esa jarayon ichida umumiy xotirani ulashadi."},
                {"raqam": 2, "savol": "Round-Robin rejalashtirish algoritmini misolda tushuntiring.", "turi": "amaliy",
                 "javob_namunasi": "Har bir jarayonga teng vaqt kvanti beriladi, navbat bilan bajariladi."},
            ]},
            {"variant": 2, "savollar": [
                {"raqam": 1, "savol": "Tupikga (deadlock) olib keluvchi 4 shartni sanab bering.", "turi": "nazariy",
                 "javob_namunasi": "O'zaro istisno, ushlab turish va kutish, oldindan tortib olmaslik, aylanma kutish."},
            ]},
        ],
    },
)

# 9. Kurs ishi mavzulari
SAMPLES['kurs-ishi-mavzulari'] = (
    {**TATU_META_QISQA, 'fan_nomi': 'Dasturiy injiniring', 'soha': '05.00.00 — Texnika fanlari',
     'mutaxassislik': '05.01.01 — Tizimli tahlil', 'talim_bosqichi': 'Bakalavriat', 'ish_turi': 'Kurs ishi'},
    {
        "mavzular": [
            {"raqam": 1, "mavzu": "Onlayn kutubxona boshqaruv tizimini loyihalash",
             "tavsif": "Kitoblar katalogi, foydalanuvchi ro'yxati va ijara tizimini o'z ichiga olgan veb-ilova.",
             "tavsiya_adabiyot_yonalishi": "Dasturiy ta'minot muhandisligi, ma'lumotlar bazasi loyihalash"},
            {"raqam": 2, "mavzu": "Talabalar reyting tizimini avtomatlashtirish",
             "tavsif": "Fanlar bo'yicha ballarni yig'ish va reytingni avtomatik hisoblovchi tizim.",
             "tavsiya_adabiyot_yonalishi": "Algoritmlar, veb-dasturlash"},
            {"raqam": 3, "mavzu": "Mobil ilova orqali ob-havo monitoringi",
             "tavsif": "API orqali ob-havo ma'lumotlarini olib, foydalanuvchiga taqdim etuvchi mobil ilova.",
             "tavsiya_adabiyot_yonalishi": "Mobil dasturlash, API integratsiyasi"},
        ],
    },
)


def html_escape(text):
    return (str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _table_html(headers, rows):
    if not rows:
        return ''
    out = ['<table style="width:100%;border-collapse:collapse;margin:10px 0;">']
    out.append('<tr>' + ''.join(f'<th style="border:1px solid #cbd5e1;padding:6px;background:#f1f5f9;text-align:left;">{html_escape(h)}</th>' for h in headers) + '</tr>')
    for row in rows:
        out.append('<tr>' + ''.join(f'<td style="border:1px solid #cbd5e1;padding:6px;">{html_escape(c)}</td>' for c in row) + '</tr>')
    out.append('</table>')
    return ''.join(out)


def build_html_preview(doc_type, metadata, content):
    label = {
        'sillabus': 'SILLABUS', 'ishchi-dastur': "ISHCHI O'QUV DASTURI", 'uslubiy-qollanma': "USLUBIY QO'LLANMA",
        'fan-dasturi': 'FAN DASTURI', 'mustaqil-talim': "MUSTAQIL TA'LIM TOPSHIRIQLARI",
        'baholash-mezonlari': 'BAHOLASH MEZONLARI', 'test-savollari': 'TEST SAVOLLARI',
        'imtihon-savollari': 'IMTIHON SAVOLLARI', 'kurs-ishi-mavzulari': 'KURS ISHI MAVZULARI',
    }[doc_type]
    parts = [
        '<div style="text-align:center;font-weight:bold;margin-bottom:4px;">',
        "O'ZBEKISTON RESPUBLIKASI OLIY TA'LIM, FAN VA INNOVATSIYALAR VAZIRLIGI</div>",
        f'<div style="text-align:center;margin-bottom:16px;">{html_escape(metadata.get("universitet"))}</div>',
        f'<div style="text-align:center;font-weight:bold;font-size:1.15em;margin:20px 0;">{html_escape(metadata.get("fan_nomi"))} fanidan {label}</div>',
    ]
    if content.get('fanning_maqsadi'):
        parts.append(f'<p><b>Fanning maqsadi.</b> {html_escape(content["fanning_maqsadi"])}</p>')
    if content.get('kirish'):
        parts.append(f'<p><b>Kirish.</b> {html_escape(content["kirish"])}</p>')
    if content.get('fanning_vazifalari'):
        parts.append('<p><b>Fanning vazifalari:</b></p><ul>' +
                     ''.join(f'<li>{html_escape(v)}</li>' for v in content['fanning_vazifalari']) + '</ul>')
    if content.get('mavzular_rejasi'):
        parts.append('<p><b>Mavzular rejasi</b></p>' + _table_html(
            ['Hafta', 'Mavzu', 'Mazmuni'],
            [(m.get('hafta'), m.get('mavzu'), m.get('mazmuni')) for m in content['mavzular_rejasi'][:5]]))
    if content.get('mashgulotlar'):
        for m in content['mashgulotlar'][:2]:
            parts.append(f'<p><b>{m.get("tartib_raqami")}-mashg\'ulot: {html_escape(m.get("mavzu"))}</b><br>'
                        f'{html_escape(m.get("maqsad"))}</p>')
    if content.get('topshiriqlar'):
        for t in content['topshiriqlar'][:2]:
            parts.append(f'<p><b>{t.get("raqam")}-topshiriq: {html_escape(t.get("mavzu"))}</b><br>{html_escape(t.get("topshiriq_matni"))}</p>')
    if content.get('baholash_turlari'):
        for bt in content['baholash_turlari']:
            parts.append(f'<p><b>{html_escape(bt.get("tur"))}</b> (maks. {bt.get("maksimal_ball")} ball)</p>' +
                        _table_html(['Mezon', 'Ball', 'Tavsif'],
                                   [(m.get('mezon'), m.get('ball'), m.get('tavsif')) for m in bt.get('mezonlar', [])]))
    if content.get('savollar'):
        for q in content['savollar'][:3]:
            variantlar = q.get('variantlar', {})
            opts = ''.join(f'<div style="margin-left:16px;">{k}) {html_escape(v)}{" ✓" if k == q.get("togri_javob") else ""}</div>'
                          for k, v in variantlar.items() if v)
            parts.append(f'<p><b>{q.get("raqam")}. {html_escape(q.get("savol"))}</b>{opts}</p>')
    if content.get('variantlar') and doc_type == 'imtihon-savollari':
        for v in content['variantlar'][:1]:
            parts.append(f'<p><b>{v.get("variant")}-variant</b></p>')
            for s in v.get('savollar', []):
                parts.append(f'<p>{s.get("raqam")}. {html_escape(s.get("savol"))}</p>')
    if content.get('mavzular') and doc_type == 'kurs-ishi-mavzulari':
        parts.append(_table_html(['№', 'Mavzu', 'Tavsif'],
                                 [(m.get('raqam'), m.get('mavzu'), m.get('tavsif')) for m in content['mavzular']]))
    if content.get('umumiy_tamoyillar'):
        parts.append(f'<p>{html_escape(content["umumiy_tamoyillar"])}</p>')
    bm = content.get('baholash_mezoni')
    if bm:
        parts.append('<p><b>Baholash mezoni:</b> ' +
                     ', '.join(f'{k.replace("_", " ")} — {v}%' for k, v in bm.items()) + '</p>')
    ad = content.get('adabiyotlar')
    if ad and ad.get('asosiy'):
        parts.append('<p><b>Asosiy adabiyotlar:</b></p><ul>' +
                     ''.join(f'<li>{html_escape(a)}</li>' for a in ad['asosiy']) + '</ul>')
    parts.append('<p style="text-align:center;color:#94a3b8;font-size:0.85em;margin-top:24px;">— hujjat davom etadi —</p>')
    return '\n'.join(parts)


def main():
    os.makedirs(SAMPLES_DOCX_DIR, exist_ok=True)
    os.makedirs(SAMPLES_HTML_DIR, exist_ok=True)
    for doc_type, (metadata, content) in SAMPLES.items():
        docx_path = os.path.join(SAMPLES_DOCX_DIR, f"namuna_{doc_type.replace('-', '_')}.docx")
        save_docx(doc_type, content, metadata, out_path=docx_path)
        html = build_html_preview(doc_type, metadata, content)
        html_path = os.path.join(SAMPLES_HTML_DIR, f"{doc_type.replace('-', '_')}.html")
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"OK  {doc_type:22s} -> {docx_path}, {html_path}")


if __name__ == '__main__':
    main()
