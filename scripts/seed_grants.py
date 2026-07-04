# -*- coding: utf-8 -*-
"""Seed data — 15 real grant/stipendiya dasturi (taxminiy keyingi sikl muddatlari).

Ikki xil ishga tushirish:
  1) Admin paneldan: /admin/grants → "🌱 Seed" tugmasi (tavsiya etiladi).
  2) Serverda qo'lda:  DATABASE_URL=... python scripts/seed_grants.py

Idempotent: source_id UNIQUE + ON CONFLICT DO NOTHING — qayta bosish/qayta
ishga tushirish dublikat yaratmaydi. Muddatlar (deadline) taxminiy — admin
panelda aniqlashtiring.
"""
import json
import os
import sys

_D = lambda *docs: json.dumps([
    {'name': n, 'required': r, 'tip': t, 'template_url': ''}
    for n, r, t in docs], ensure_ascii=False)

SEED_GRANTS = [
    dict(source_id='seed-daad-research', title='DAAD Research Grants — Doctoral Programmes',
         title_uz='DAAD tadqiqot grantlari (doktorantura)',
         organization='DAAD — German Academic Exchange Service', country='Germaniya',
         funding_type='full', academic_levels=['PhD'],
         deadline='2026-10-15', stipend='€1,300/oy', duration='12–48 oy',
         lang="Nemis (B1) yoki Ingliz (IELTS 6.0+)",
         url='https://www.daad.de/en/study-and-research-in-germany/scholarships/',
         tags=['Yevropa', 'doktorantura', 'tadqiqot'],
         desc=("Germaniya akademik almashinuv xizmati (DAAD) doktorantlar va yosh "
               "tadqiqotchilar uchun to'liq moliyalashtirilgan tadqiqot grantlari beradi. "
               "Grant Germaniyadagi istalgan davlat universiteti yoki tadqiqot institutida "
               "PhD tadqiqotini qamrab oladi.\n\n"
               "Oylik stipendiya, sog'liq sug'urtasi, yo'l xarajatlari va oilaviy "
               "nafaqalar kiradi. Nemis tili kursi ham bepul taklif etiladi."),
         req=("Magistr diplomi (yoki tugatish arafasida), ilmiy rahbar bilan oldindan "
              "kelishuv (supervision confirmation), kuchli tadqiqot taklifi, "
              "til sertifikati."),
         ben="€1,300/oy stipendiya, sug'urta, yo'l puli, til kursi, oilaviy nafaqa.",
         tips=("Avval Germaniyadagi professor bilan bog'lanib, supervision letter oling — "
               "bu arizaning eng muhim qismi. Tadqiqot taklifingizni aniq metodologiya "
               "bilan yozing."),
         docs=_D(('Motivation Letter', True, "1-2 sahifa, aniq maqsad"),
                 ('Research Proposal', True, '5-10 sahifa, metodologiya bilan'),
                 ('Supervision Confirmation', True, 'Germaniyadagi professordan'),
                 ('Tavsiyanomalar (2 ta)', True, 'Professorlardan'),
                 ('Til sertifikati', True, 'IELTS 6.0+ yoki TestDaF'))),
    dict(source_id='seed-fulbright', title='Fulbright Foreign Student Program',
         title_uz='Fulbright xorijiy talabalar dasturi',
         organization='U.S. Department of State', country='AQSH',
         funding_type='full', academic_levels=['Master', 'PhD'],
         deadline='2027-02-10', stipend="To'liq ta'minot", duration='1–2 yil',
         lang='Ingliz (TOEFL iBT 80+ / IELTS 6.5+)',
         url='https://uz.usembassy.gov/education-culture/fulbright/',
         tags=['AQSH', 'magistratura', 'doktorantura'],
         desc=("Fulbright — AQSH hukumatining eng nufuzli akademik almashinuv dasturi. "
               "O'zbekistonlik yosh mutaxassislarga AQSH universitetlarida magistratura "
               "yoki tadqiqot olib borish uchun to'liq grant beriladi.\n\n"
               "Barcha yo'nalishlar qabul qilinadi (tibbiyotdan tashqari)."),
         req="Bakalavr diplomi, ingliz tili, O'zbekiston fuqaroligi, 2 yillik ish tajribasi afzallik.",
         ben="To'liq o'qish puli, oylik stipendiya, aviachipta, sug'urta, kitob puli.",
         tips=("Insholaringizda (personal statement) o'z hikoyangizni Amerika ta'limi "
               "bilan bog'lang. Qaytib kelib O'zbekistonga qanday hissa qo'shishingizni "
               "aniq yozing — bu Fulbright'ning asosiy mezoni."),
         docs=_D(('Personal Statement', True, "O'z hikoyangiz, 1-2 sahifa"),
                 ('Study Objective', True, 'Aniq akademik maqsad'),
                 ('Tavsiyanomalar (3 ta)', True, 'Professor yoki ish beruvchidan'),
                 ('TOEFL/IELTS', True, 'TOEFL 80+ yoki IELTS 6.5+'),
                 ('Diplom va transkript', True, 'Ingliz tiliga tarjima bilan'))),
    dict(source_id='seed-erasmus-mundus', title='Erasmus Mundus Joint Masters',
         title_uz="Erasmus Mundus qo'shma magistratura dasturlari",
         organization='European Commission', country='Yevropa Ittifoqi',
         funding_type='full', academic_levels=['Master'],
         deadline='2027-01-15', stipend='€1,400/oy', duration='1–2 yil',
         lang='Ingliz (IELTS 6.5+, dasturga qarab)',
         url='https://www.eacea.ec.europa.eu/scholarships/emjmd-catalogue_en',
         tags=['Yevropa', 'magistratura', 'almashinuv'],
         desc=("Erasmus Mundus — kamida 2-3 Yevropa universitetida o'qiladigan qo'shma "
               "magistratura dasturlari. Har semestr boshqa mamlakatda o'qish imkoniyati.\n\n"
               "150 dan ortiq dastur katalogi mavjud — o'z sohangizga mosini tanlang."),
         req='Bakalavr diplomi, ingliz tili sertifikati, dasturga qarab qo\'shimcha talablar.',
         ben="To'liq o'qish puli, €1,400/oy, yo'l va viza xarajatlari, sug'urta.",
         tips=("Bir vaqtning o'zida 3 tagacha dasturga ariza topshirishingiz mumkin — "
               "imkoniyatdan foydalaning. Motivatsiya xatini har dasturga moslashtiring."),
         docs=_D(('Motivation Letter', True, 'Har dasturga alohida moslang'),
                 ('CV (Europass)', True, 'Europass formatida'),
                 ('Tavsiyanomalar (2 ta)', True, ''),
                 ('IELTS/TOEFL', True, 'IELTS 6.5+'),
                 ('Diplom', True, 'Tarjima + notarial tasdiq'))),
    dict(source_id='seed-turkiye-burslari', title='Türkiye Bursları',
         title_uz='Turkiya stipendiyalari (Türkiye Bursları)',
         organization='Turkish Government', country='Turkiya',
         funding_type='full', academic_levels=['Master', 'PhD'],
         deadline='2027-02-20', stipend='Magistr ₺9,000 / PhD ₺12,000 oyiga',
         duration='2–4 yil + 1 yil til kursi', lang="Turk (bepul o'rgatiladi) yoki Ingliz",
         url='https://www.turkiyeburslari.gov.tr/',
         tags=['Turkiya', 'magistratura', 'doktorantura'],
         desc=("Turkiya hukumatining to'liq stipendiyasi: o'qish, yotoqxona, sug'urta, "
               "aviachipta va oylik stipendiya. Birinchi yil bepul turk tili kursi.\n\n"
               "700 dan ortiq universitet va dastur ichidan tanlash mumkin."),
         req='Diplom (magistr uchun bakalavr, PhD uchun magistr), yosh chegarasi: magistr 30, PhD 35.',
         ben="To'liq ta'minot: o'qish, yotoqxona, stipendiya, sug'urta, chipta, til kursi.",
         tips=("Niyat xatida Turkiya bilan akademik aloqangizni ko'rsating. "
               "Intervyu bosqichiga tayyorlaning — ko'pincha onlayn o'tkaziladi."),
         docs=_D(('Niyat xati', True, 'Aniq maqsad va reja'),
                 ('Diplom va transkript', True, ''),
                 ('Tavsiyanoma', True, 'Kamida 1 ta'),
                 ('Pasport nusxasi', True, ''))),
    dict(source_id='seed-el-yurt-umidi', title="El-yurt umidi Foundation Scholarships",
         title_uz="«El-yurt umidi» jamg'armasi stipendiyalari",
         organization="O'zbekiston Vazirlar Mahkamasi huzuridagi «El-yurt umidi» jamg'armasi",
         country="O'zbekiston", funding_type='full',
         academic_levels=['Master', 'PhD'],
         deadline='2026-09-30', stipend="To'liq ta'minot", duration='1–4 yil',
         lang='Qabul qilingan universitet talabiga ko\'ra (IELTS 6.5–7.0+)',
         url='https://eyuf.uz/',
         tags=["O'zbekiston", 'xorij', 'davlat granti'],
         desc=("«El-yurt umidi» jamg'armasi O'zbekiston fuqarolarini dunyoning yetakchi "
               "universitetlarida (QS TOP-300) magistratura va doktorantura bosqichlarida "
               "o'qitish uchun to'liq moliyalashtiradi.\n\n"
               "Bitiruvchilar davlat tashkilotlarida kamida 5 yil ishlash majburiyatini oladi."),
         req="O'zbekiston fuqaroligi, TOP universitetdan qabul xati (unconditional offer), til sertifikati.",
         ben="O'qish, yashash, stipendiya, aviachipta, viza — barchasi qoplanadi.",
         tips=("Avval universitetdan unconditional offer oling — jamg'arma arizasi "
               "shundan keyin kuchli bo'ladi. Davlat xizmatidagi kelajak rejangizni "
               "aniq yozing."),
         docs=_D(('Universitet qabul xati', True, 'Unconditional offer afzal'),
                 ('IELTS/TOEFL', True, 'Universitet talabiga ko\'ra'),
                 ('Diplom', True, ''),
                 ('Tavsiyanomalar', True, '2 ta'),
                 ('Motivatsiya xati', True, 'Davlatga xizmat rejasi bilan'))),
    dict(source_id='seed-chevening', title='Chevening Scholarships',
         title_uz='Chevening stipendiyasi (Buyuk Britaniya)',
         organization='UK Foreign, Commonwealth & Development Office',
         country='Buyuk Britaniya', funding_type='full', academic_levels=['Master'],
         deadline='2026-11-03', stipend="To'liq ta'minot", duration='1 yil',
         lang='Ingliz (universitet talabi, odatda IELTS 6.5+)',
         url='https://www.chevening.org/',
         tags=['Buyuk Britaniya', 'magistratura', 'liderlik'],
         desc=("Chevening — Buyuk Britaniya hukumatining kelajak liderlariga mo'ljallangan "
               "bir yillik magistratura stipendiyasi. Istalgan UK universitetida istalgan "
               "yo'nalishda o'qish mumkin.\n\n"
               "Tanlovda liderlik salohiyati va tarmoq qurish qobiliyati asosiy mezon."),
         req='Bakalavr diplomi, 2 yil ish tajribasi (2,800 soat), 3 ta UK dasturiga ariza.',
         ben="O'qish puli, oylik stipendiya, aviachipta, viza, ko'chib o'tish nafaqasi.",
         tips=("4 ta insho yozasiz: liderlik, tarmoq, nima uchun UK, karyera rejasi. "
               "Har birida aniq misollar keltiring — umumiy gaplar rad etiladi."),
         docs=_D(('4 ta insho', True, 'Liderlik, networking, UK tanlovi, karyera'),
                 ('2 ta tavsiyanoma', True, ''),
                 ('Diplom', True, ''),
                 ('3 ta universitet arizasi', True, 'Kamida 1 offer kerak'))),
    dict(source_id='seed-mext', title='MEXT Japanese Government Scholarship',
         title_uz='MEXT — Yaponiya hukumati stipendiyasi',
         organization='Ministry of Education, Culture, Sports, Science and Technology (Japan)',
         country='Yaponiya', funding_type='full', academic_levels=['Master', 'PhD'],
         deadline='2027-05-15', stipend='¥144,000–145,000/oy', duration='2–5 yil',
         lang="Yapon yoki Ingliz (dasturga qarab)",
         url='https://www.uz.emb-japan.go.jp/',
         tags=['Yaponiya', 'magistratura', 'doktorantura'],
         desc=("MEXT — Yaponiya hukumatining to'liq stipendiyasi: o'qish puli, oylik "
               "stipendiya va aviachipta. Elchixona tavsiyasi (Embassy Recommendation) "
               "yo'li orqali ariza topshiriladi.\n\n"
               "Birinchi bosqich — hujjat tanlovi, keyin yozma imtihon va intervyu."),
         req="Diplom, 35 yoshgacha (PhD), tadqiqot rejasi (research plan), sog'liq ma'lumotnomasi.",
         ben="O'qish bepul, ¥144,000+/oy, aviachipta, viza yordami.",
         tips=("Research Plan — eng muhim hujjat: yapon professorining sohasi bilan "
               "bog'lang. Yaponiyadagi professor bilan oldindan email orqali bog'lanish "
               "katta afzallik beradi."),
         docs=_D(('Research Plan', True, '2-3 sahifa, aniq metodologiya'),
                 ('Diplom va transkript', True, ''),
                 ('Tavsiyanoma', True, 'Universitet rahbariyatidan'),
                 ("Sog'liq ma'lumotnomasi", True, 'MEXT formasi'),
                 ('Til sertifikati', False, 'JLPT yoki IELTS (ixtiyoriy, afzallik)'))),
    dict(source_id='seed-gks-korea', title='Global Korea Scholarship (GKS)',
         title_uz='GKS — Janubiy Koreya hukumati stipendiyasi',
         organization='National Institute for International Education (NIIED)',
         country='Janubiy Koreya', funding_type='full', academic_levels=['Master', 'PhD'],
         deadline='2027-02-28', stipend='₩1,000,000–1,100,000/oy',
         duration='2–4 yil + 1 yil til kursi', lang="Koreys (bepul o'rgatiladi) yoki Ingliz",
         url='https://www.studyinkorea.go.kr/',
         tags=['Koreya', 'magistratura', 'doktorantura'],
         desc=("GKS (sobiq KGSP) — Koreya hukumatining to'liq stipendiyasi. Birinchi yil "
               "koreys tili o'rganiladi (TOPIK 3+ darajaga yetish shart), keyin asosiy "
               "o'qish boshlanadi.\n\n"
               "Elchixona yoki universitet orqali ariza topshirish mumkin."),
         req='Diplom, GPA 80%+, 40 yoshgacha, sog\'liq talablari.',
         ben="O'qish, til kursi, ₩1M+/oy, aviachipta, sug'urta, ko'chib o'tish puli.",
         tips=("Universitet yo'li (University Track) raqobati elchixona yo'lidan pastroq "
               "bo'lishi mumkin. TOPIK sertifikati bo'lsa qo'shimcha ball."),
         docs=_D(('Personal Statement', True, ''),
                 ('Study Plan', True, 'Aniq reja'),
                 ('Tavsiyanomalar (2 ta)', True, ''),
                 ('Diplom + transkript', True, 'Apostil bilan'))),
    dict(source_id='seed-csc-china', title='Chinese Government Scholarship (CSC)',
         title_uz='CSC — Xitoy hukumati stipendiyasi',
         organization='China Scholarship Council', country='Xitoy',
         funding_type='full', academic_levels=['Master', 'PhD'],
         deadline='2027-03-31', stipend='¥3,000–3,500/oy', duration='2–4 yil',
         lang="Xitoy (bepul o'rgatiladi) yoki Ingliz",
         url='https://www.campuschina.org/',
         tags=['Xitoy', 'magistratura', 'doktorantura'],
         desc=("CSC — Xitoy hukumatining eng yirik stipendiya dasturi: 280 dan ortiq "
               "universitetda o'qish, yotoqxona, sug'urta va oylik stipendiya.\n\n"
               "Elchixona (Type A) yoki to'g'ridan-to'g'ri universitet (Type B) orqali "
               "ariza berish mumkin."),
         req='Diplom, magistr uchun 35, PhD uchun 40 yoshgacha, sog\'liq formasi.',
         ben="O'qish bepul, yotoqxona, ¥3,000+/oy, sug'urta.",
         tips=("Type B (universitet orqali) yo'lida qabul ehtimoli yuqoriroq. "
               "Professordan acceptance letter olish arizani sezilarli kuchaytiradi."),
         docs=_D(('Study Plan', True, 'Magistr 800+ so\'z, PhD 1500+ so\'z'),
                 ('Tavsiyanomalar (2 ta)', True, 'Professorlardan'),
                 ('Diplom + transkript', True, 'Notarial tasdiqlangan'),
                 ('Foreigner Physical Examination Form', True, ''))),
    dict(source_id='seed-stipendium-hungaricum', title='Stipendium Hungaricum',
         title_uz='Stipendium Hungaricum (Vengriya)',
         organization='Tempus Public Foundation', country='Vengriya',
         funding_type='full', academic_levels=['Master', 'PhD'],
         deadline='2027-01-15', stipend='HUF 43,700–140,000/oy', duration='2–4 yil',
         lang='Ingliz (dasturga qarab IELTS 5.5–6.5)',
         url='https://stipendiumhungaricum.hu/',
         tags=['Vengriya', 'Yevropa', 'magistratura', 'doktorantura'],
         desc=("Vengriya hukumatining O'zbekiston bilan ikki tomonlama kelishuvi asosidagi "
               "to'liq stipendiyasi — har yili yuzlab o'rin ajratiladi.\n\n"
               "O'qish bepul, stipendiya, yotoqxona yoki uy-joy nafaqasi va sug'urta beriladi."),
         req="Diplom, til sertifikati, O'zbekiston bo'yicha milliy nominatsiya (OTM vazirligi orqali).",
         ben="O'qish bepul, oylik stipendiya, yotoqxona, sug'urta.",
         tips=("Milliy nominatsiya bosqichini o'tkazib yubormang — O'zbekistonda "
               "qo'shimcha ro'yxatdan o'tish talab qilinadi. 2 ta dastur tanlash mumkin."),
         docs=_D(('Motivation Letter', True, ''),
                 ('Diplom + transkript', True, 'Ingliz tarjimasi bilan'),
                 ('Til sertifikati', True, ''),
                 ("Sog'liq ma'lumotnomasi", True, ''))),
    dict(source_id='seed-mininnovatsiya', title='State Scientific Research Grants (Uzbekistan)',
         title_uz="Davlat ilmiy-tadqiqot grantlari (O'zbekiston)",
         organization="Oliy ta'lim, fan va innovatsiyalar vazirligi",
         country="O'zbekiston", funding_type='research',
         academic_levels=['Research', 'PhD', 'Postdoc'],
         deadline='2026-11-01', stipend='Loyihaga qarab', duration='1–3 yil',
         lang="O'zbek / Rus",
         url='https://mininnovation.uz/',
         tags=["O'zbekiston", 'tadqiqot', 'davlat granti'],
         desc=("Fundamental, amaliy va innovatsion tadqiqot loyihalari uchun davlat "
               "grantlari. Yosh olimlar, doktorantlar va ilmiy jamoalar uchun alohida "
               "yo'nalishlar mavjud.\n\n"
               "Arizalar elektron platforma orqali qabul qilinadi."),
         req="Ilmiy daraja yoki doktorantura, tadqiqot loyihasi, ilmiy jamoa (jamoaviy grantlar uchun).",
         ben='Loyiha byudjeti: jihozlar, ish haqi, safar xarajatlari.',
         tips=("Loyiha maqsadini davlat ustuvor yo'nalishlariga bog'lang. "
               "Byudjetni asoslab, aniq ko'rsatkichlar (KPI) bilan yozing."),
         docs=_D(('Loyiha arizasi', True, 'Platforma orqali'),
                 ('Ilmiy jamoa CV lari', True, ''),
                 ('Byudjet smetasi', True, 'Asoslangan'),
                 ('Ilmiy dalolatnoma', False, 'Oldingi natijalar'))),
    dict(source_id='seed-cern-summer', title='CERN Summer Student Programme',
         title_uz='CERN yozgi talabalar dasturi',
         organization='CERN', country='Shveysariya',
         funding_type='full', academic_levels=['Master'],
         deadline='2027-01-31', stipend='CHF 92/kun', duration='8–13 hafta',
         lang='Ingliz',
         url='https://careers.cern/summer',
         tags=['Shveysariya', 'fizika', 'yozgi dastur'],
         desc=("Dunyoning eng yirik zarralar fizikasi laboratoriyasida 8-13 haftalik "
               "yozgi tadqiqot dasturi. Fizika, kompyuter fanlari va muhandislik "
               "talabalari uchun.\n\n"
               "Kunlik nafaqa, yo'l xarajatlari va sug'urta qoplanadi."),
         req="Bakalavrning 3-kursi yoki magistratura talabasi, fizika/IT/muhandislik yo'nalishi.",
         ben='CHF 92/kun, yo\'l puli, sug\'urta, mashhur olimlar ma\'ruzalari.',
         tips=("Tavsiyanomalar hal qiluvchi — sizni yaxshi biladigan professorlardan "
               "oling. GPA va loyiha tajribangizni ko'rsating."),
         docs=_D(('CV', True, ''),
                 ('Motivation Letter', True, ''),
                 ('Transkript', True, ''),
                 ('Tavsiyanomalar (2 ta)', True, ''))),
    dict(source_id='seed-swiss-excellence', title='Swiss Government Excellence Scholarships',
         title_uz='Shveysariya hukumati mukammallik stipendiyalari',
         organization='FCS — Federal Commission for Scholarships', country='Shveysariya',
         funding_type='full', academic_levels=['PhD', 'Postdoc'],
         deadline='2026-12-15', stipend='CHF 1,920/oy', duration='1–3 yil',
         lang='Universitet talabiga ko\'ra (Ingliz/Nemis/Fransuz)',
         url='https://www.sbfi.admin.ch/scholarships_eng',
         tags=['Shveysariya', 'doktorantura', 'postdoc'],
         desc=("Shveysariya hukumatining PhD va postdoc tadqiqotchilar uchun "
               "stipendiyasi. Shveysariyaning 10 ta kanton universiteti, ETH Zurich va "
               "EPFL da tadqiqot olib borish mumkin.\n\n"
               "Ariza O'zbekistondagi Shveysariya elchixonasi orqali topshiriladi."),
         req='Magistr diplomi (PhD uchun), Shveysariyadagi professordan qabul xati, tadqiqot rejasi.',
         ben='CHF 1,920/oy, o\'qish puli, sug\'urta, uy-joy nafaqasi, aviachipta.',
         tips=("Professor bilan oldindan bog'lanib, support letter oling — busiz ariza "
               "ko'rib chiqilmaydi. Research proposal ni professor sohasiga moslang."),
         docs=_D(('Research Proposal', True, '5 sahifagacha'),
                 ('Professor support letter', True, 'Shveysariyadan'),
                 ('Tavsiyanomalar (2 ta)', True, ''),
                 ('Diplom', True, ''))),
    dict(source_id='seed-australia-awards', title='Australia Awards Scholarships',
         title_uz='Australia Awards stipendiyalari',
         organization='Australian Government DFAT', country='Avstraliya',
         funding_type='full', academic_levels=['Master', 'PhD'],
         deadline='2027-04-30', stipend="To'liq ta'minot + AUD 500+/ikki hafta",
         duration='2–4 yil', lang='Ingliz (IELTS 6.5+)',
         url='https://www.dfat.gov.au/people-to-people/australia-awards',
         tags=['Avstraliya', 'magistratura', 'doktorantura'],
         desc=("Avstraliya hukumatining rivojlanayotgan mamlakatlar uchun to'liq "
               "stipendiyasi: o'qish, yashash nafaqasi, aviachipta va sug'urta.\n\n"
               "Rivojlanish sohalariga (ta'lim, sog'liqni saqlash, boshqaruv, qishloq "
               "xo'jaligi) ustuvorlik beriladi."),
         req='Diplom, IELTS 6.5+, ish tajribasi afzallik, rivojlanish sohasiga aloqadorlik.',
         ben="To'liq o'qish, yashash nafaqasi, chipta, sug'urta, tayyorlov kurslari.",
         tips=("O'z sohangizni O'zbekiston rivojlanishiga bog'lab yozing — bu dasturning "
               "asosiy g'oyasi. Qaytish majburiyati bor (kamida 2 yil)."),
         docs=_D(('Ariza formasi (OASIS)', True, 'Onlayn'),
                 ('IELTS', True, '6.5+'),
                 ('Diplom + transkript', True, ''),
                 ('Tavsiyanomalar', True, '2 ta'))),
    dict(source_id='seed-pearson-toronto', title='Lester B. Pearson International Scholarships',
         title_uz='Lester B. Pearson stipendiyasi (Toronto universiteti)',
         organization='University of Toronto', country='Kanada',
         funding_type='full', academic_levels=['Master'],
         deadline='2026-11-30', stipend="To'liq ta'minot (4 yil)", duration='4 yil',
         lang='Ingliz (IELTS 6.5+ / TOEFL 100+)',
         url='https://future.utoronto.ca/pearson/',
         tags=['Kanada', 'bakalavr', 'liderlik'],
         desc=("Toronto universitetining xalqaro talabalar uchun eng nufuzli granti — "
               "bakalavr bosqichini to'liq qoplaydi (o'qish, kitoblar, yashash).\n\n"
               "Maktab tomonidan nominatsiya talab qilinadi; liderlik va ijtimoiy "
               "faollik asosiy mezon."),
         req="Maktab bitiruvchisi (yoki 2026-27da bitiruvchi), maktab nominatsiyasi, kuchli akademik natijalar.",
         ben="4 yillik to'liq ta'minot: o'qish, kitoblar, yotoqxona.",
         tips=("Avval maktabingizdan nominatsiya oling — bu majburiy birinchi qadam. "
               "Inshoda o'z jamiyatingizga ta'siringizni ko'rsating."),
         docs=_D(('Maktab nominatsiyasi', True, 'Majburiy birinchi qadam'),
                 ('UofT arizasi', True, 'OUAC orqali'),
                 ('Insho', True, 'Liderlik haqida'),
                 ('IELTS/TOEFL', True, ''))),
]


def upsert_seed(cur, items):
    """Insert seed grants; returns number of newly added rows. Idempotent."""
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..'))
    from blueprints.grants import generate_slug, get_country_flag
    added = 0
    for it in items:
        slug = generate_slug(it['title_uz'] or it['title'])
        cur.execute("SELECT 1 FROM grants WHERE slug = %s", (slug,))
        if cur.fetchone():
            slug = f"{slug}-{it['source_id'].split('-')[-1]}"
        levels = it['academic_levels']
        cur.execute("""
            INSERT INTO grants
                (source_id, title, title_uz, slug, organization, country,
                 country_flag, funding_type, academic_levels, scientific_fields,
                 description, requirements, benefits, documents_checklist,
                 application_tips, application_deadline, stipend_amount, duration,
                 language_requirements, source_url, tags, is_active, is_featured,
                 academic_level, provider)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
            ON CONFLICT (source_id) WHERE source_id IS NOT NULL DO NOTHING
            RETURNING id
        """, (it['source_id'], it['title'], it['title_uz'], slug,
              it['organization'], it['country'], get_country_flag(it['country']),
              it['funding_type'], levels, it.get('fields', []),
              it['desc'], it['req'], it['ben'], it['docs'], it['tips'],
              it['deadline'], it['stipend'], it['duration'], it['lang'],
              it['url'], it.get('tags', []),
              it['source_id'] in ('seed-daad-research', 'seed-fulbright',
                                  'seed-el-yurt-umidi', 'seed-chevening'),
              levels[0] if levels else None, it['organization']))
        if cur.fetchone():
            added += 1
    return added


if __name__ == '__main__':
    try:
        import psycopg2
    except ImportError:
        sys.exit('pip install psycopg2-binary')
    url = os.environ.get('DATABASE_URL')
    if not url:
        sys.exit('DATABASE_URL not set')
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
            from blueprints.grants import _ensure_schema
            _ensure_schema(cur)
            n = upsert_seed(cur, SEED_GRANTS)
        conn.commit()
        print(f'Seed OK: {n} ta yangi grant ({len(SEED_GRANTS)} tadan)')
    finally:
        conn.close()
