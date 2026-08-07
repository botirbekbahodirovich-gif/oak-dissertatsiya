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

## 8. Ierarxiya va yetishmayotgan ota yo'nalishlar

Daraja va ota shifr **koddan hisoblanadi**:

```
XX.00.00 → level 1 (fan tarmog'i)
XX.YY.00 → level 2 (ixtisoslik guruhi)
XX.YY.ZZ → level 3 (ixtisoslik)
```

Manbada deyarli faqat **level 3** yozuvlar bor (288 ta; bittasi — yuqoridagi
buzilgan `10.00.00`). Guruh (level 2) nomlari manbada **umuman yo'q**.

### Qaror: sun'iy ota yozuv YARATILMAYDI

DB cheklovlari tekshirildi: `scientific_fields.parent_id` — NULLable,
`ON DELETE RESTRICT`, `level` esa mustaqil ustun (daraxt chuqurligidan
hisoblanmaydi). Ya'ni model bir necha xatti-harakatga ruxsat beradi. Tanlangan
strategiya:

| Holat | Nima bo'ladi |
|---|---|
| `parent_exists` | ota bazada bor → to'g'ridan-to'g'ri bog'lanadi |
| `parent_missing_but_known_code` | guruh nomi noma'lum → yozuv **eng yaqin mavjud ajdodga** (fan tarmog'iga) bog'lanadi, haqiqiy guruh shifri `missing_parent_code` ustunida saqlanadi |
| `parent_unresolvable` | hech qanday ajdod yo'q → yozuv **import qilinmaydi** |

Nima uchun aynan shunday:

* **Sun'iy nom yaratilmaydi.** `05.01.00` uchun "Informatika guruhi" kabi nom
  o'ylab topilmaydi — u rasmiy manbadan kelishi kerak.
* **Yozuv yo'qolmaydi.** Maqola aniq `05.01.05` ixtisosligiga bog'lanishi
  mumkin; guruh nomi keyin to'ldiriladi.
* **Soxta ildiz yaratilmaydi.** `parent_id = NULL` bilan kiritilgan ixtisoslik
  daraxtda fan tarmog'i kabi ko'rinardi — bu noto'g'ri, shuning uchun
  `parent_unresolvable` yozuvlar butunlay o'tkazib yuboriladi.
* **UI faqat SHIFRNI ko'rsatadi.** `/maqolalar/yonalish/<slug>` sahifasida
  "Bu ixtisoslik **05.01.00** guruhiga tegishli. Guruhning rasmiy nomi hali
  OAK manbasidan olinmagan" deb yoziladi. Hech qanday soxta soha nomi
  chiqmaydi.

Guruh nomi olingach: uni `data/oak/normalized/oak_specialties_approved.json`
ga level-2 yozuv sifatida qo'shing va importni qayta ishga tushiring —
u idempotent, bolalarni yangi otaga qayta bog'laydi.

---

## 9. Nima aynan yetishmayapti

### 16 ta ixtisoslik guruhi (level 2) — 70 ta yozuvga ta'sir qiladi

```
01.01.00  01.02.00  01.03.00  01.04.00
05.01.00  05.02.00  05.03.00  05.05.00  05.06.00
05.07.00  05.08.00  05.09.00  05.10.00
06.01.00  06.02.00  06.03.00
```

(Butun manba bo'yicha 20 ta; qolgan 4 tasi — `03.01.00`, `05.04.00` — faqat
review'dagi yozuvlarga tegishli.)

### 1 ta fan tarmog'i (level 1) — 2 ta yozuv import qilinmadi

```
21.00.00  — repozitoriydagi 22 ta tarmoq ro'yxatida YO'Q
21.01.00, 21.02.00 — uning guruhlari
```

Import qilinmagan yozuvlar: `21.01.08` (Qurolli Kuchlar front orti),
`21.02.23` (Harbiy tibbiyot). Uchinchisi — `21.01.06` — allaqachon review'da
(yopilmagan qo'shtirnoq).

`app.py:SPECIALTY_NAMES` da 22 ta tarmoq bor (01–19, 22, 23, 24) va 21-tarmoq
("Harbiy fanlar") yo'q. Uning rasmiy nomi hech qanday mavjud manbada topilmadi.

**Kerak:** rasmiy OAK registridan yuqoridagi 16 guruh va 1 tarmoq nomi.

---

## 10. 22 ta mavjud fan tarmog'i bilan birlashtirish

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
# 1) migratsiya (bir marta)
psql "$DATABASE_URL" -f migrations/008_scientific_field_provenance.sql

# 2) 22 ta fan tarmog'i
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/scientific_fields.json --apply

# 3) OAK ixtisosliklari — avval dry-run
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/oak/normalized/oak_specialties_approved.json

# 4) qo'llash
DATABASE_URL=... python scripts/seed_scientific_fields.py \
    --file data/oak/normalized/oak_specialties_approved.json --apply
```

> `--apply` berilmasa script **dry-run** rejimida ishlaydi. Bu ataylab: 289
> yozuvli datasetni tasodifan productionga tushirib yuborish oson.

Kutilayotgan natija (xotiradagi model ustida tekshirilgan):

```
Yangi        : 253
Yangilangan  : 0
O'zgarishsiz : 0
Konflikt     : 0
Ota topilmadi: 2      (21.01.08, 21.02.23)

RASMIY GURUH NOMI YO'Q — 70 ta yozuv eng yaqin mavjud tarmoqqa bog'landi
```

Ikkinchi run: `Yangi 0 / Yangilangan 0 / O'zgarishsiz 253`.

Migratsiya 008 `scientific_fields` ga faqat NULLable ustunlar qo'shadi
(`raw_name`, `source_line`, `source_sha256`, `review_status`, `reviewed_by`,
`reviewed_at`, `missing_parent_code`) va uchta indeks. Bitta tranzaksiya,
idempotent, `DROP`/`DELETE`/`UPDATE` yo'q. Rollback fayl oxirida izohda.

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
| `tests/test_oak_import.py` | migratsiya 008 xavfsizligi, ota hal qilish, idempotentlik, review gate |
| `tests/test_oak_field_ui.py` | katta taksonomiya UI, sahifalash, missing-parent ko'rinishi, admin filtrlari, review navbati |

Manba fayl o'zgarsa `test_the_curated_list_is_pinned_to_the_committed_source`
darhol yiqiladi — jimgina boshqa dataset importga tushib ketmaydi.

---

## 15. Ma'lum cheklovlar

* **Manba rasmiy emas.** U qo'lda yig'ilgan; 34 ta yozuv odam tasdig'ini
  kutmoqda. Rasmiy OAK registri olingach, ideal yo'l — butun datasetni o'shandan
  qayta qurish.
* **16 guruh + 1 tarmoq nomi yo'q** (§9). Shu sababli 70 ta ixtisoslik daraxtda
  bir daraja yuqorida ko'rinadi va 2 tasi umuman import qilinmagan.
* **Transliteratsiya rasmiy tarjima emas.** 102 ta yozuvda noaniqlik warningi
  bor (asosan `ё`, `ц`, `ъ`).
* **Lokal DB yo'q** (eskirgan Neon kredensiallari — alohida texnik qarz), shu
  sababli import xotiradagi model ustida tekshirilgan; haqiqiy DB ustida
  bajarilmagan.
* **Manbadagi imlo xatolari saqlangan** (`Темир йоъллар`, kichik harf bilan
  boshlanish) — ular tuzatilmaydi, chunki normalizatsiya qoidalari ro'yxatida
  yo'q va ular manba muammosining belgisi bo'lishi mumkin.
