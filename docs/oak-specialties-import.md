# OAK ixtisosliklari — parsing va import

Fan yo'nalishlari taksonomiyasining OAK ixtisosliklari qismi qayerdan keladi,
qanday tozalanadi, nima uchun ba'zi yozuvlar import qilinmaydi va yangilanish
qanday bajariladi.

Modulning qolgan qismi: **`docs/articles-module.md`**.

---

## 1. Manba fayl

| | |
|---|---|
| Fayl | `data/oak/source/oak_barcha_shifrlar.md` |
| Kelib chiqishi | OAK.UZ saytidan **qo'lda** yig'ilgan matn |
| SHA-256 | `5e492b0fa56d5cde4414be718f66d1bfaca9b97f5dd7142107622574365c1792` |
| Hajm | 22 057 bayt, 586 qator, CRLF |
| E'lon qilingan yozuvlar | `Jami: 289 ta` |

Fayl repozitoriyga **vendor qilingan** (asl joyi foydalanuvchi mashinasida edi).
Sabab: parsing takrorlanadigan bo'lishi kerak va repozitoriy hech qachon
absolute path'ga (`/mnt/data/...`) bog'lanib qolmasligi kerak.

Format har bir yozuv uchun bir xil:

```
01.01.01  –  Математик анализ
```

kod, ikkita probel, **en dash** (U+2013), ikkita probel, nom. Yozuvlar orasida
bo'sh qator. Uchta xizmat qatori bor: sarlavha, `Jami: 289 ta`, `====` chiziq.

### Manba RASMIY DATASET EMAS

Faylni tekshirganda quyidagi haqiqiy nuqsonlar topildi — ular **tuzatilmadi**,
faqat belgilandi:

| Nuqson | Soni |
|---|---|
| Ochilgan, lekin yopilmagan qo'shtirnoq (nom kesilgan) | 15 |
| `ихтисослиги бўйича` konteksti nomga aralashgan | 6 |
| Bir xil nom boshqa kodda takrorlanadi | 6 yozuv (3 nom) |
| Qo'lda tekshirilgan tugallanmagan nom | 5 |
| Kod darajasi ziddiyatli (`10.00.00`) | 1 |
| Begona jumla (`Диссертация мавзуси ... рақам:`) | 1 |
| Non-breaking space | 1 |
| Kirill so'z ichida lotin apostrof (`ко'рсатиш`) | 1 |
| Nom kichik harf bilan boshlanadi | 1 |

Manba fayl **hech qachon tahrirlanmaydi**. Barcha tozalash parser ichida
bajariladi va har bir o'zgarish qayd etiladi.

---

## 2. Parser

```bash
python scripts/parse_oak_specialties.py \
    --input data/oak/source/oak_barcha_shifrlar.md \
    --output-dir data/oak
```

Bayroqlar: `--dry-run` (yozmaydi), `--parsed-at <ISO>` (deterministik output
uchun vaqtni qotiradi), `--allow-count-mismatch`.

Xususiyatlari:

* UTF-8; bo'sh qatorlar va xizmat qatorlari yozuv deb olinmaydi;
* kod regex bilan ajratiladi (`^\d{2}\.\d{2}\.\d{2}$`);
* har bir yozuvda **manba qatori raqami** saqlanadi;
* deterministik — bir xil input bir xil output beradi;
* manbadagi `Jami: N ta` bilan parse qilingan soni mos kelmasa **non-zero
  exit code** bilan to'xtaydi.

### Chiqadigan fayllar

```
data/oak/
├── source/oak_barcha_shifrlar.md              raw manba (o'zgarmaydi)
├── raw/oak_specialties_raw.json               289 ta: kod + ASL nom + qator
├── normalized/oak_specialties_approved.json   255 ta — IMPORT SHUNDAN
├── review/oak_specialties_review_required.json 34 ta — odam ko'rishi kerak
├── review/oak_specialties_rejected.json       0 ta
└── reports/oak_specialties_validation.json    to'liq validatsiya hisoboti
```

---

## 3. Ishonchlilik holatlari

| Holat | Ma'nosi | Soni |
|---|---|---|
| `clean` | kod va nom aniq, texnik buzilish yo'q | 216 |
| `normalized` | faqat texnik format tozalandi | 39 |
| `review_required` | odam ko'rishi shart | 34 |
| `rejected` | kod yoki nomni ajratib bo'lmaydi | 0 |

Production seed faqat `clean` + `normalized` + qo'lda tasdiqlangan
(`manual_approved`) yozuvlarni oladi.

---

## 4. Nima AVTOMATIK o'zgartiriladi

Faqat texnik format. Har bir o'zgarish `normalization_notes` da qayd etiladi:

| Note | O'zgarish |
|---|---|
| `outer_quotes_removed` | nomni **to'liq** o'rab turgan juft `“ ”` / `« »` / `" "` |
| `markdown_bold_removed` | boshidagi/oxiridagi `**` |
| `nbsp_replaced` | non-breaking space → oddiy probel |
| `outer_whitespace_trimmed` | bosh/oxirgi bo'sh joy |
| `inner_whitespace_collapsed` | ketma-ket probellar → bitta |
| `dash_unified` | `–` / `—` → `-` |
| `apostrophe_unified` | `‘ ’ ʻ ʼ ´ \`` → ASCII `'` |
| `trailing_period_removed` | oxiridagi bitta ortiqcha nuqta |

## 5. Nima AVTOMATIK o'zgartirilMAYDI

* kesilgan so'z **to'ldirilmaydi**;
* rasmiy nom **taxmin qilinmaydi**;
* `Европа` uzun nomga aylantirilmaydi;
* `Мамлакат`, `Камёб`, `Композицион`, `Тери`, `Радиотехника` davom
  ettirilmaydi;
* internetdan yoki boshqa manbadan nom **almashtirilmaydi**;
* AI bilan matn **"to'g'rilanmaydi"**;
* manbadagi imlo xatosi (`Темир йоъллар`, kichik harf bilan boshlanish)
  tuzatilmaydi — u xato belgisi bo'lishi mumkin.

**Asl matn har doim `raw_name` da saqlanadi** — JSON'da ham, bazada ham
(`scientific_fields.raw_name`).

---

## 6. Review sabablari

`review_required` ga olib keladigan qoidalar (chastota bilan):

| Sabab | Soni |
|---|---|
| `unbalanced_quotes_possible_truncation` | 15 |
| `contains_specialty_context_suffix` | 6 |
| `duplicate_name_with_<kod>` | 6 |
| `manually_flagged_possible_truncation` | 5 |
| `code_level_conflicts_with_specialty_list` | 1 |
| `foreign_text_in_name` | 1 |
| `name_ends_with_colon` | 1 |

### Qo'lda tekshirilgan ro'yxat (`CURATED_SUSPECTS`)

Beshta yozuvda **hech qanday texnik signal yo'q** — qo'shtirnoq balansli,
begona matn yo'q — lekin nom mazmunan tugallanmagan:

`01.02.02` (Машиналар), `02.00.07` (Композицион), `02.00.19` (Камёб),
`05.08.01` (Мамлакат), `10.00.04` (Европа).

Umumiy morfologik qoida bilan ularni ajratib bo'lmaydi: "Алгебра" ham,
"Композицион" ham bitta so'z, lekin birinchisi to'liq nom, ikkinchisi sifat.
Shuning uchun ro'yxat **manbaning aynan shu revizyasiga (SHA-256) bog'langan**
— boshqa fayl berilsa u qo'llanmaydi. Ro'yxatda **nom taklif qilinmaydi**,
faqat "odam ko'rsin" deb belgilanadi.

### Alohida diqqat: `10.00.00`

Manbada `10.00.00 – Матншунослик ва адаби манбашунослик` degan yozuv bor.
`10.00.00` — bu **fan tarmog'i** shifri (bazada u "Filologiya fanlari"), lekin
nomi ixtisoslik nomi va `10.00.10` bilan deyarli bir xil. Uni jimgina import
qilish mavjud tarmoq nomini almashtirib yuborardi. Shuning uchun u
`code_level_conflicts_with_specialty_list` sababi bilan review'ga chiqadi va
test bazadagi "Filologiya fanlari" o'zgarmaganini tekshiradi.

---

## 7. Transliteratsiya

`utils/oak_translit.py` — ko'rsatiladigan nomlar uchun **alohida** modul.

**Nega mavjud `utils/transliterate.py` ishlatilmadi:** u ataylab qidiruv uchun
yozilgan, natijani butunlay kichik harfga tushiradi, `ц/щ/ъ/ь/ы` ni bilmaydi
va o'z docstringida "aniq/qaytariladigan transliteratsiya emas" deb yozilgan.
Uni o'zgartirish butun saytdagi qidiruvga ta'sir qilardi (`h ≠ x` ajratilishi
qidiruv uchun kritik).

Asosiy mosliklar:

```
ў → o'    ғ → g'    қ → q     ҳ → h     х → x
ш → sh    ч → ch    ж → j     ё → yo    ю → yu    я → ya
ц → s     щ → sh    ъ → '     ь → (tushiriladi)   ы → i
е → ye (so'z boshida) / e (so'z ichida)
```

**Apostrof ASCII `'`**, U+2018 (`o‘`) emas. Sabab: qidiruv so'rovni ASCII
apostrofga keltiradi, lekin ustunni normalizatsiya qilmaydi — nom `o‘` bilan
saqlansa, `o'` bo'yicha qidiruv uni topa olmaydi.

**Bu rasmiy tarjima emas.** Rasmiy nom `name_uz_cyrillic` da va u har doim
haqiqat manbai. Noaniq holatlar `transliteration_warnings` da:

| Warning | Soni |
|---|---|
| `cyrillic_yo_is_ambiguous` | 55 |
| `cyrillic_ts_is_ambiguous` | 26 |
| `cyrillic_hard_sign_is_ambiguous` | 24 |
| `cyrillic_ye_at_word_start` | 5 |
| `cyrillic_soft_sign_dropped` | 2 |

Jami **102 ta yozuvda** kamida bitta warning bor. Warning yozuvni production
seed'dan **chiqarib tashlamaydi** — kirill nomning o'zi aniq bo'lsa yozuv
o'tadi, warning esa hisobotda qoladi.

---

## 8. Ierarxiya qoidasi

Daraja va ota shifr **koddan hisoblanadi**:

```
XX.00.00 → level 1 (fan tarmog'i)
XX.YY.00 → level 2 (ixtisosliklar guruhi)
XX.YY.ZZ → level 3 (ixtisoslik)
```

> **QAT'IY QOIDA.** Rasmiy 2-daraja guruh mavjud bo'lsa, 3-daraja ixtisoslik
> HECH QACHON to'g'ridan-to'g'ri 1-daraja fan tarmog'iga bog'lanmaydi.
>
> *Official OAK level-3 specialties must never be attached directly to a
> level-1 branch when an official level-2 group exists.*

Guruh bazada bo'lmasa yozuv **import qilinmaydi** (`parent_pending_official_group`)
va hisobotda ko'rsatiladi. Noto'g'ri joyga qo'yilgandan ko'ra ko'rinmagani
yaxshi — sun'iy ota ham, "eng yaqin tarmoq" fallback'i ham yo'q.

### Olib tashlangan vaqtinchalik yechim

Oldingi bosqichda guruh nomlari noma'lum edi va ixtisosliklar **eng yaqin
mavjud ajdodga** (fan tarmog'iga) bog'lanib, haqiqiy guruh shifri
`missing_parent_code` ustunida saqlanardi. Bu ataylab vaqtinchalik edi.
Rasmiy guruh nomlari olingandan keyin fallback `resolve_parent()` dan
**butunlay olib tashlandi**; mavjud qatorlarni tuzatish uchun
`--repair-hierarchy` rejimi qo'shildi (§11).

`missing_parent_code` ustuni saqlanadi, lekin endi u faqat eski fallback izini
topish uchun kerak — muvaffaqiyatli tuzatishdan keyin barcha qatorlarda `NULL`.

### Guruhi bo'lmagan tarmoqlar

OAK klassifikatsiyasida faqat **01, 05, 06 va 21** tarmoqlarida ixtisosliklar
guruhi bor. Qolgan tarmoqlarda ixtisoslik shifri `XX.00.ZZ` ko'rinishida
bo'ladi va uning otasi to'g'ridan-to'g'ri tarmoq (`XX.00.00`) — bu rasmiy
tuzilma, fallback emas. Bunday yozuvning `level` qiymati koddan kelib chiqib
3 bo'ladi, daraxt chuqurligi esa 2.

---

## 9. Rasmiy 1- va 2-daraja dataseti

**Manba:** «Олий малакали илмий ва илмий-педагог кадрлар ихтисосликлари
рўйхати» (OAK, 2021)
`https://oak.uz/userfiles/files/Ixtisosliklar_ro'yxati_2021.pdf`
SHA-256 `39e4e3fd2641f359de4e36c0dc16f7c6862bc96e4c78db9b422df3355e21ed6a`

Hujjat bitta PDF ichida jadvalni **uch marta** takrorlaydi: o'zbek kirill,
rus, ingliz. Bizga o'zbek kirill bo'limi kerak.

**Dataset:** `data/oak/normalized/oak_specialty_groups.json` — 20 ta yozuv
(1 ta fan tarmog'i + 19 ta guruh). Har birida to'liq provenance:
`source_name` (hujjat + bet raqami), `source_url`, `source_page`,
`source_year`, `source_sha256`, `source_updated_at`, `review_status:
official_source`.

Qayta yaratish: `scripts/extract_oak_groups.py` (PDF repozitoriyga
qo'shilmagan — 1 MB, rasmiy sayt hosting qiladi; script hash'ni tekshiradi).

### Topilgan nomlar

| Kod | Rasmiy nom (uz-Cyrl) | Lotin | Bet |
|---|---|---|---|
| 21.00.00 | Ҳарбий фанлар | Harbiy fanlar | 42 |
| 01.01.00 | Математика | Matematika | 4 |
| 01.02.00 | Механика | Mexanika | 4 |
| 01.03.00 | Астрономия | Astronomiya | 5 |
| 01.04.00 | Физика | Fizika | 5 |
| 05.01.00 | Ахборот технологиялари, бошқарув ва компьютер графикаси | Axborot texnologiyalari, boshqaruv va kompyuter grafikasi | 13 |
| 05.02.00 | Машинасозлик ва машинашунослик. … | Mashinasozlik va mashinashunoslik. … | 14 |
| 05.03.00 | Асбобсозлик, метрология ва ахборот-ўлчов асбоблари ва тизимлари | Asbobsozlik, metrologiya … | 16 |
| 05.04.00 | Радиотехника ва алоқа | Radiotexnika va aloqa | 16 |
| 05.05.00 | Энергетика ва электротехника. … | Energetika va elektrotexnika. … | 16 |
| 05.06.00 | Тўқимачилик ва енгил саноат материаллари ва буюмлари технологияси | To'qimachilik va yengil sanoat … | 17 |
| 05.07.00 | Қишлоқ хўжалиги ишлаб чиқаришини механизациялаш технологияси | Qishloq xo'jaligi … mexanizasiyalash | 18 |
| 05.08.00 | Транспорт | Transport | 18 |
| 05.09.00 | Қурилиш | Qurilish | 19 |
| 05.10.00 | Инсон фаолияти хавфсизлиги | Inson faoliyati xavfsizligi | 20 |
| 06.01.00 | Агрономия | Agronomiya | 21 |
| 06.02.00 | Зоотехния | Zootexniya | 22 |
| 06.03.00 | Ўрмон хўжалиги | O'rmon xo'jaligi | 23 |
| 21.01.00 | Ҳарбий-назарий фанлар | Harbiy-nazariy fanlar | 42 |
| 21.02.00 | Ҳарбий-махсус фанлар | Harbiy-maxsus fanlar | 43 |

Nomlar hujjatdan **aynan ko'chirilgan**; lotin varianti
`utils/oak_translit.py` orqali hosil qilingan va rasmiy tarjima emas.

### 21.00.00 — Ҳарбий фанлар

Rasmiy 2021 ro'yxatida **23 ta fan tarmog'i** bor: 01–19, 21, 22, 23, 24
(20 yo'q). Repozitoriydagi `app.py:SPECIALTY_NAMES` da 22 tasi bor edi —
aynan **21** yetishmayotgan edi. Bu son ko'r-ko'rona 23 ga o'zgartirilmadi:
u rasmiy hujjatdagi tarmoq sarlavhalarini sanash orqali aniqlandi.

`SPECIALTY_NAMES` **o'zgartirilmadi**: u OAK **jurnallarini** seed qilish
uchun ishlatiladi va kalitlari `seed_data.JOURNALS_BY_SPECIALTY` bilan bir
xil bo'lishi kerak (21-tarmoq uchun OAK jurnali ro'yxati yo'q). 21.00.00
alohida, rasmiy manbali datasetdan keladi — bu ikkala ro'yxatning kelib
chiqishi ham to'g'ri qoladi.

Ikkinchi rasmiy manba — «Ихтисосликлар паспорти»
(`https://oak.uz/userfiles/files/Ixtisosliklar%20pasporti.pdf`, SHA-256
`06be7263900db6b4fea196b593627fc33e831cd786b7abad354867c9936331d9`) — 16 ta
guruh nomini **tasdiqlaydi**, lekin faqat rus tilida va unda 21-tarmoq yo'q
(u 01–19 + 22 ni qamraydi). Shuning uchun asosiy manba sifatida 2021 yilgi
ro'yxat ishlatildi.

---

## 10. Mavjud fan tarmoqlari bilan birlashtirish

Tarmoqlar `data/scientific_fields.json` da (manba: `app.py:SPECIALTY_NAMES` +
`seed_data.JOURNALS_BY_SPECIALTY`). Ixtisosliklar ular ostiga **kod bo'yicha**
qo'shiladi.

Kafolatlar (testlar bilan qo'riqlangan):

* mavjud ID'lar saqlanadi (upsert `code` bo'yicha, hech narsa qayta
  yaratilmaydi);
* maqola assotsiatsiyalari buzilmaydi (`article_scientific_fields` ga
  tegilmaydi);
* dublikat yaratilmaydi (ikkinchi run — 0 ta yozuv);
* **nom to'qnashuvi jimgina almashtirilmaydi**: importer `conflict` deb qayd
  etadi va yozuvni o'zgartirmaydi. Majburlash uchun `--update-names` kerak;
* datasetdan yo'qolgan yozuv **avtomatik o'chirilmaydi**;
* manba kelib chiqishi har bir qatorda saqlanadi (`source_line`,
  `source_sha256`, `review_status`, `raw_name`).

---

## 11. Import

```bash
# 1) migratsiyalar (bir marta)
psql "$DATABASE_URL" -f migrations/007_create_article_catalog.sql
psql "$DATABASE_URL" -f migrations/008_scientific_field_provenance.sql

# 2) repozitoriy fan tarmoqlari (22 ta)
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/scientific_fields.json --apply

# 3) RASMIY 1- va 2-daraja (21-tarmoq + 19 guruh) — ixtisosliklardan OLDIN
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/oak/normalized/oak_specialty_groups.json --apply

# 4) ixtisosliklar — avval dry-run
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/oak/normalized/oak_specialties_approved.json --repair-hierarchy

# 5) qo'llash
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/oak/normalized/oak_specialties_approved.json \
    --repair-hierarchy --apply
```

> `--apply` berilmasa script **dry-run** rejimida ishlaydi.

**Tartib muhim:** guruhlar ixtisosliklardan oldin kiritilishi shart, aks
holda ixtisosliklar `pending` bo'lib qoladi (bu xato emas — keyingi run
ularni oladi).

### `--repair-hierarchy`

Eski vaqtinchalik bog'lanishlarni rasmiy otaga ko'chiradi. Ikkita manba
bo'yicha ishlaydi: `missing_parent_code` to'ldirilgan qatorlar, va shifri
rasmiy guruhni ko'rsatadigan, lekin tarmoqqa bog'langan qatorlar.

Kafolatlar:

* **Maqola bog'lanishlariga tegilmaydi** — yozuvning `id` si o'zgarmaydi,
  faqat `parent_id` yangilanadi, shuning uchun `article_scientific_fields`
  butunligicha qoladi. Hisobot bog'lanishlar sonini oldin/keyin solishtiradi.
* **Slug o'zgarmaydi** — mavjud URL'lar ishlashda davom etadi.
* Hamma narsa bitta tranzaksiyada; dry-run hech nima yozmaydi.
* Ikkinchi run — 0 o'zgarish.

Hisobot: `specialties_reparented`, `records_unblocked`,
`article_links_before/after`, `article_links_preserved`,
`still_missing_parents`.

Kutilayotgan natija (xotiradagi model ustida tekshirilgan):

```
1) 22 tarmoq       : created 22
2) rasmiy guruhlar : created 20   (21.00.00 + 19 guruh)
3) 255 ixtisoslik  : created 255, conflict 0, pending 0
4) repair          : still_missing_parents = []

level bo'yicha: {1: 23, 2: 19, 3: 255}   — jami 297 yozuv
05.01.05 zanjiri: 05.00.00 -> 05.01.00 -> 05.01.05
```

Ikkinchi run: 0 yozuv, 0 reparent.

---

## 12. Review workflow

`review/oak_specialties_review_required.json` dagi yozuvda **uchta** maydonni
to'ldiring (uchalasi ham shart):

```json
"approved_name_uz_cyrillic": "Композицион материаллар технологияси",
"reviewer": "botirbek",
"reviewed_at": "2026-08-07",
"review_note": "OAK registri, 2026-08-07 da tekshirildi"
```

So'ng parserni qayta ishga tushiring:

```bash
python scripts/parse_oak_specialties.py
```

U tasdiqni o'qiydi, yozuvni `manual_approved` holatiga o'tkazadi, lotin nomini
avtomatik hosil qiladi va approved datasetga qo'shadi. Tasdiq review faylida
ham qoladi — keyingi re-parse uni yo'qotmaydi.

Tasdiq manbaning **aynan o'sha revizyasiga** bog'langan: fayl o'zgarsa
(SHA boshqa) tasdiq qo'llanmaydi va yozuv navbatda qoladi, script buni
`stale_review_codes` sifatida aytadi.

**Admin ko'rinishi:** `/admin/scientific-fields/review` — faqat o'qish uchun.
U shifr, asl nom, normalizatsiya nomzodi, manba qatori va sabablarni
ko'rsatadi, hamda yuqoridagi tartibni sahifada tushuntiradi. Bu ataylab yangi
moderatsiya tizimi **emas**: tasdiqlangan nom fayl orqali git tarixiga
tushishi kerak.

**Review qilinmagan yozuv import qilinmaydi** — `normalize_entries()`
`review_status` ni tekshiradi va `review_required` ni `SeedError` bilan rad
etadi.

---

## 13. UI

### `/maqolalar/yonalishlar`

Ikki rejim:

* **qidiruvsiz** — daraxt. Faqat fan tarmoqlari ko'rinadi, ichki
  yo'nalishlar tugma ortida. Hech qanday daraja sukut bo'yicha ochiq emas
  (05-tarmoqda 44 ta ixtisoslik bor).
* **qidiruv yoki tarmoq filtri bilan** — tekis, **sahifalangan** ro'yxat
  (40/sahifa). Har bir natijada ota konteksti alohida ko'rsatiladi.

Qo'shimcha: kod prefiksi bo'yicha qidiruv (`05.01`), fan tarmog'i filtri,
"faqat maqolasi mavjud" filtri, ichki yo'nalishlar soni, maqolalar soni.

Nom ko'rsatish: **lotin asosiy**, rasmiy kirill ikkilamchi metadata
(`lang="uz-Cyrl"`). Guruh nomi noma'lum bo'lsa faqat shifr.

### `/admin/scientific-fields`

Default — **sahifalangan jadval** (50/sahifa), 275+ yozuv bitta sahifaga
render qilinmaydi. Filtrlar: nom, shifr prefiksi, daraja, tasdiqlash holati,
manba, faollik, "guruh nomi yo'q". Ustunlar: shifr, nom (+ kirill + asl),
ota konteksti, daraja, tasdiqlash, manba, manba qatori, maqolalar soni.
Daraxt ko'rinishi `?view=daraxt` bilan.

---

## 14. Testlar

| Fayl | Nima tekshiradi |
|---|---|
| `tests/test_oak_translit.py` | transliteratsiya, o'zbek maxsus harflari, noaniqlik warninglari |
| `tests/test_oak_parser.py` | parsing, normalizatsiya qoidalari, review sabablari, SHA, determinizm, haqiqiy manba ustidagi sonlar |
| `tests/test_oak_import.py` | migratsiya 008, rasmiy guruh dataseti, ota hal qilish (fallback yo'q), `--repair-hierarchy`, idempotentlik, review gate |
| `tests/test_oak_field_ui.py` | katta taksonomiya UI, sahifalash, missing-parent ko'rinishi, admin filtrlari, review navbati |

Manba fayl o'zgarsa `test_the_curated_list_is_pinned_to_the_committed_source`
darhol yiqiladi — jimgina boshqa dataset importga tushib ketmaydi.

---

## 15. Ma'lum cheklovlar

* **Manba rasmiy emas.** U qo'lda yig'ilgan; 34 ta yozuv odam tasdig'ini
  kutmoqda. Rasmiy OAK registri olingach, ideal yo'l — butun datasetni o'shandan
  qayta qurish.
* ~~16 guruh + 1 tarmoq nomi yo'q~~ — **hal qilindi**: rasmiy 2021 yilgi OAK
  ro'yxatidan olindi (§9). Endi `missing official parent = 0`.
* **Transliteratsiya rasmiy tarjima emas.** 102 ta yozuvda noaniqlik warningi
  bor (asosan `ё`, `ц`, `ъ`).
* **Lokal DB yo'q** (eskirgan Neon kredensiallari — alohida texnik qarz), shu
  sababli import va ierarxiya tuzatish xotiradagi model ustida tekshirilgan;
  haqiqiy DB ustida bajarilmagan.
* **34 ta review yozuvi hali navbatda.** Rasmiy 2021 ro'yxatida ularning
  to'liq nomlari bor, lekin ular jadval kataklarida qatorlarga bo'linib
  ketgan va ishonchli avtomatik ajratish uchun alohida ish talab qiladi —
  bu bosqichda ular AVTOMATIK tuzatilmadi (§12 dagi qo'lda tasdiqlash oqimi
  o'z kuchida).
* **Manbadagi imlo xatolari saqlangan** (`Темир йоъллар`, kichik harf bilan
  boshlanish) — ular tuzatilmaydi, chunki normalizatsiya qoidalari ro'yxatida
  yo'q va ular manba muammosining belgisi bo'lishi mumkin.
