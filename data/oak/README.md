# OAK ixtisosliklari dataseti

Bu papka OAK (Oliy attestatsiya komissiyasi) ilmiy ixtisosliklar ro'yxatining
qayta ishlangan holatini saqlaydi. To'liq jarayon:
**`docs/oak-specialties-import.md`**.

```
data/oak/
├── source/                       raw manba, o'zgartirilmagan holda
│   └── oak_barcha_shifrlar.md    OAK.UZ dan qo'lda yig'ilgan, 289 ta yozuv
├── raw/
│   └── oak_specialties_raw.json  har bir qator: kod + ASL nom + qator raqami
├── normalized/
│   └── oak_specialties_approved.json   PRODUCTION dataset (import shundan)
├── review/
│   ├── oak_specialties_review_required.json   odam ko'rishi kerak
│   └── oak_specialties_rejected.json          ajratib bo'lmagan yozuvlar
└── reports/
    └── oak_specialties_validation.json        to'liq validatsiya hisoboti
```

## Manba fayl RASMIY DATASET EMAS

`source/oak_barcha_shifrlar.md` — OAK.UZ saytidan qo'lda yig'ilgan matn.
Unda haqiqiy nuqsonlar bor va ular ATAYIN tuzatilmagan:

* 15 ta yozuvda ochilgan, lekin yopilmagan qo'shtirnoq (nom kesilgan);
* 1 ta yozuvda begona jumla (`Диссертация мавзуси ... рақам:`);
* 6 ta yozuvda `ихтисослиги бўйича` konteksti nomga aralashgan;
* 1 ta yozuvda kod darajasi ziddiyatli (`10.00.00` — fan tarmog'i shifri,
  lekin nomi ixtisoslik nomi);
* 3 ta nom boshqa kodda takrorlanadi;
* bitta non-breaking space, bitta lotin apostrof kirill so'z ichida.

Manba fayl **hech qachon tahrirlanmaydi**. Barcha tozalash parser ichida
bajariladi va har bir o'zgarish `normalization_notes` da qayd etiladi.

## Fayllarni qayta yaratish

```bash
python scripts/parse_oak_specialties.py \
    --input data/oak/source/oak_barcha_shifrlar.md \
    --output-dir data/oak
```

Script deterministik: bir xil input bir xil output beradi. Manbadagi
`Jami: N ta` bilan parse qilingan yozuvlar soni mos kelmasa, script
**non-zero exit code** bilan to'xtaydi.

## Qaysi fayl importga tushadi

Faqat `normalized/oak_specialties_approved.json`. Unda `review_status`
maydoni bor va u faqat quyidagilardan biri bo'lishi mumkin:

| review_status | ma'nosi |
|---|---|
| `auto_clean` | manbada texnik buzilish yo'q edi |
| `auto_normalized` | faqat format tozalandi (qo'shtirnoq, bo'sh joy, tire) |
| `manual_approved` | odam ko'rib, rasmiy nomni tasdiqladi |

`review_required` yozuvlar import qilinmaydi — importer ularni rad etadi.

## Review qilish

`review/oak_specialties_review_required.json` dagi yozuvda uchta maydonni
to'ldiring (uchalasi ham shart):

```json
"approved_name_uz_cyrillic": "Композицион материаллар технологияси",
"reviewer": "botirbek",
"reviewed_at": "2026-08-07"
```

So'ng parserni qayta ishga tushiring — u tasdiqni o'qib, yozuvni approved
datasetga o'tkazadi va lotin nomini avtomatik hosil qiladi.

Tasdiq manbaning **aynan o'sha revizyasiga** (SHA-256) bog'langan. Manba fayl
o'zgarsa tasdiq avtomatik qo'llanmaydi va yozuv navbatda qoladi — chunki asl
matn boshqacha bo'lishi mumkin.

Admin ko'rinishi: `/admin/scientific-fields/review` (faqat o'qish).

## Nom hech qachon o'ylab topilmaydi

Parser kesilgan nomni to'ldirmaydi, rasmiy nomni taxmin qilmaydi va AI bilan
"to'g'rilamaydi". Asl matn har doim `raw_name` da saqlanadi va bazaga ham
o'sha holda yoziladi (`scientific_fields.raw_name`).
