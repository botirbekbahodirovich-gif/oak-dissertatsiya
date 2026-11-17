# Issue'lar (2026-11-17)

`olimlar_test` bazasida `TEST_DATABASE_URL` bilan DB-testlar ishga tushirilganda
topildi. To'rttasi ham shu kuni branch'larda tuzatildi. Hech biri hali `main`'ga
yoki prod'ga chiqmagan.

| # | Issue | Holat | Branch / commit |
|---|---|---|---|
| 1 | Birlashtirish `dissertation_institutions` FK'sini tanimaydi | tuzatildi | `fix/duplicate-merge-chain` 4e0c314c |
| 2 | Qayta yo'naltirilgan juftlik ko'rib chiqilmasdan birlashadi | tuzatildi, (a) varianti | `fix/duplicate-merge-chain` ec1c8b23 |
| 3 | `test_retraction_filter` fixture'i | tuzatildi, faqat test | `feat/articles-uz` bf3f4f73 |
| 4 | Bo'sh bazada `dissertations.oak_id` yo'q | tuzatildi, migratsiya 026 | `feat/articles-uz` 4fa505c3 |
| 5 | Sarlavhalarda HTML entity va LaTeX xom ko'rinadi | ochiq | — |

`fix/duplicate-merge-chain` prod'ga alohida deploy bilan chiqadi. U maqolalar
paketiga kirmaydi.

Testlarni qayta ishlatish:

```bash
APP_ENV=testing TEST_DATABASE_URL=postgresql://…@localhost:5432/olimlar_test \
  python3 -m unittest tests.test_merge_duplicates_dbgated tests.test_article_sync_dbgated
```

---

## 1. Dublikat birlashtirish `dissertation_institutions` FK'sini tanimaydi

**Jiddiylik:** yuqori. Prod'da dublikat birlashtirish hozir ishlamaydi.

**Holat: tuzatildi** (`fix/duplicate-merge-chain` 4e0c314c).
- Yutqazgan yozuvning bog'lanishlari g'olibga
  `INSERT … SELECT … ON CONFLICT (dissertation_id, institution_id) DO NOTHING`
  bilan ko'chiriladi.
- G'olibning o'z asosiy muassasasiga teng bog'lanish ko'chirilmaydi:
  `institution_backfill.py` ham asosiy muassasani hamkor qilib yozmaydi.
- So'ng yutqazgan yozuvning bog'lanishlari o'chiriladi.
- Har bir bog'lanish `data_quality_fix_log` ga
  `merge_link_repointed` / `merge_link_dropped` kodi bilan yoziladi.
- DB-testlar: oddiy ko'chirish, umumiy hamkor dublikatga aylanmasligi,
  asosiy muassasa. `test_known_fk_list_matches_the_live_schema` yana o'tadi.

**Belgi:** `tests.test_merge_duplicates_dbgated…test_known_fk_list_matches_the_live_schema`

```
RuntimeError: merge refused: found foreign key(s) into dissertations(id) that this
script does not know how to repoint: [('dissertation_institutions', 'dissertation_id')]
```

**Sabab:**
- Migratsiya `021_institution_links.sql` yangi jadval qo'shgan:
  `dissertation_institutions(dissertation_id REFERENCES dissertations(id) ON DELETE CASCADE)`.
- `data_quality/repository.py` dagi `KNOWN_FK_REFERENCES_TO_DISSERTATIONS` va
  `merge_dissertations()` bu jadvalni bilmaydi.
- `scripts/merge_duplicates.py:300` har birlashtirishdan oldin
  `assert_no_unknown_fk_references()` ni chaqiradi. 021 qo'llangan bazada (prod)
  u har safar birlashtirishni rad etadi.
- Ma'lumot buzilmaydi: himoya mexanizmi to'g'ri ishlayapti.

**Tuzatish yo'nalishi:**
- `merge_dissertations()` ga yangi qadam qo'shilsin: yutqazgan yozuvning
  `dissertation_institutions` qatorlarini g'olibga ko'chirish.
- PK `(dissertation_id, institution_id)` bo'yicha to'qnashuv bo'lsa, ortiqcha
  qator o'chirilsin. Bu `bookmarks` qadamidagi bilan bir xil naqsh.
- Har ko'chirish `data_quality_fix_log` ga yozilsin.
- `KNOWN_FK_REFERENCES_TO_DISSERTATIONS` va
  `docs/data_quality_dissertation_fk_inventory.md` yangilansin.
- To'qnashuv va oddiy ko'chirish uchun DB-testlar qo'shilsin.

## 2. Qayta yo'naltirilgan nomzod juftligi qayta ko'rib chiqilmasdan birlashtiriladi

**Jiddiylik:** o'rta.

**Holat: tuzatildi** (`fix/duplicate-merge-chain` ec1c8b23). Pastdagi (a)
variant tanlandi. Admin tasdig'i faqat u ko'rgan juftlikka tegishli.
- Birlashish natijasida a'zosi o'zgargan `confirmed` yoki `rejected` juftlik
  `pending` ga qaytadi, `reviewed_by` va `reviewed_at` tozalanadi.
  Sxemadagi `CHECK` cheklovida alohida `pending_review` holati yo'q.
- (winner, x) allaqachon mavjud bo'lsa, (loser, x) o'chiriladi. Qolgan qator
  o'z holatini saqlaydi, chunki uning a'zolari o'zgarmagan.
- `merged` qatorlar yopiq tarix sifatida qoladi.
- Har bir o'zgarish `data_quality_fix_log.issue_code` ga yoziladi:
  `merge_pair_repointed`, `merge_pair_requeued`, `merge_pair_dropped` yoki
  `merge_pair_closed`.
- Bitta CLI ishga tushirishida ham `pending` ga qaytgan juftlik birlashmaydi:
  `execute_one` uni qulf ostida qayta o'qiydi.
- DB-testlar:
  - zanjir (A,B)+(B,C), bitta CLI ishga tushirishi ichida ham;
  - uchburchak (A,B)+(B,C)+(A,C);
  - teskari tartib: B qolgan holat va C qolgan holat;
  - `rejected` juftlik.

**Belgi:** `tests.test_merge_duplicates_dbgated…test_already_merged_record_cannot_be_merged_again`

```
AssertionError: True is not false   (decision.ok)
```

**Ssenariy:**
1. A, B, C yozuvlari bor. Tasdiqlangan nomzodlar: (A,B) va (B,C).
2. (A,B) birlashtiriladi: B → A ga qo'shiladi.
3. `merge_dissertations()` ning 4-qadami (B,C) nomzodini (A,C) ga
   qayta yo'naltiradi. Uning `status` i `'confirmed'` bo'lib qoladi.
4. Keyingi ishga tushirishda (A,C) uchun `plan_merge()` `ok=True` qaytaradi:
   A ham, C ham `merged_into_id` ga ega emas.

**Muammo:**
- Admin B≈C ni tasdiqlagan, A≈C ni emas.
- Qayta yo'naltirilgan juftlik odam ko'rmasdan birlashtiriladi.
- Test buni "allaqachon birlashtirilgan" xatosi kutib tekshiradi.

**Hal qilish kerak bo'lgan savol:** to'g'ri xatti-harakat qaysi?
- **(a) Tavsiya:** 4-qadamda qayta yo'naltirilgan qatorning `status` i
  `'pending'` ga qaytarilsin, ya'ni u qayta ko'rib chiqishga tushsin. Test shunga
  moslab yangilansin.
- **(b)** Tranzitiv birlashtirishga ruxsat beriladi. Unda test noto'g'ri faraz
  qilgan va o'zi tuzatiladi.

## 3. `test_retraction_filter` fixture'i noto'g'ri

**Jiddiylik:** past. Faqat test xatosi, ilova kodi to'g'ri.

**Holat: tuzatildi** (`feat/articles-uz` bf3f4f73).
- Fixture endi `status='checked'` va maqola snapshot'i bilan yoziladi.
- Retraction o'zgarishi bo'lmagan qator chiqmasligi ham tekshiriladi.
- Ilova kodiga tegilmadi.

**Belgi:** `tests.test_article_sync_dbgated…DashboardQueryDbTests.test_retraction_filter`

```
AssertionError: False is not true
```

**Sabab:**
- Test `article_metadata_syncs` ga `status` va `article_updated_at_snapshot`
  ustunlarisiz qator qo'shadi.
- `services/article_sync/persistence.py` dagi `list_dashboard()`
  (`_LATEST_SYNC_CTE`) `has_retraction=True` filtrida faqat `status='checked'`
  bo'lgan va eskirmagan (stale emas) qatorlarni qaytaradi.

**Tuzatish yo'nalishi:**
- Fixture'da `status='checked'` qo'yilsin.
- `article_updated_at_snapshot` maqolaning `updated_at` iga teng qilinsin.
  Qo'shni testlardagi `make_sync()` yordamchisidan foydalanish mumkin.

## 4. Bo'sh bazada startup migratsiyalari yiqiladi: `dissertations.oak_id` yo'q

**Jiddiylik:** past. Prod va staging'ga ta'sir qilmaydi: ularda ustun bor.
Yangi lokal yoki test bazani sozlashni buzadi.

**Holat: tuzatildi** (`feat/articles-uz` 4fa505c3).
- `migrations/026_dissertations_oak_id.sql` qo'shildi:
  - `ADD COLUMN IF NOT EXISTS oak_id INTEGER`;
  - `CREATE UNIQUE INDEX IF NOT EXISTS dissertations_oak_id_key`;
  - `CREATE INDEX IF NOT EXISTS idx_dissertations_oak_id`.
- Nomlar va tur prod bilan bir xil (faqat o'qib tekshirildi), shuning uchun
  prod'da 026 no-op.
- Startup'da ustun indeksdan oldin qo'shiladi. Faqat migratsiya fayli yetmaydi,
  chunki u o'zgartiradigan jadval aynan shu yiqilayotgan tranzaksiyada
  yaratiladi.
- Bo'sh sxemada sinov o'tkazildi:
  - tuzatishsiz 13 ta jadval chiqdi;
  - tuzatish bilan 46 ta jadval chiqdi, `/`, `/maqolalar` va `/olimlar`
    200 qaytardi;
  - 026 ikki marta xatosiz qo'llandi, `ON CONFLICT (oak_id)` ishladi.

**Belgi:** bo'sh bazada `import app` ishlatilganda `idx_dissertations_oak_id`
indeksi yaratilmaydi: `column "oak_id" does not exist`.

**Sabab:**
- `app.py:1518` startup'da `oak_id` ustuniga indeks yaratadi.
- Lekin ustun faqat qo'lda ishlatiladigan `migrate_columns.py` da qo'shiladi:
  `ADD COLUMN IF NOT EXISTS oak_id INTEGER` + `UNIQUE (oak_id)`.
- Startup kodida ham, `migrations/` da ham ustun yaratilmaydi.
- `data.py` ning bir nechta so'rovi ham `oak_id` ni o'qiydi.

**Vaqtinchalik yechim:** lokal/test bazada avval
`dissertations(oak_id INTEGER UNIQUE)` ni qo'lda yaratish.

**Tuzatish yo'nalishi:**
- Startup migratsiyasidagi `dissertations` yaratish qismiga
  `ADD COLUMN IF NOT EXISTS oak_id INTEGER` va `dissertations_oak_id_key`
  constraint'i qo'shilsin. Ikkalasi `IF NOT EXISTS` bo'lgani uchun prod'da
  hech narsa o'zgarmaydi.
- Yoki alohida qo'shimcha (additive) migratsiya yozilsin.
- Bo'sh bazada `import app` muvaffaqiyatli bo'lishini tekshiradigan DB-test
  qo'shilsin.

## 5. Maqola sarlavhalarida HTML entity va LaTeX xom ko'rinadi

**Jiddiylik:** past, kosmetik. **Holat:** ochiq. "Nashrga tayyor" sahifasini
sinashda topildi.

**Belgi:**
- OpenAlex'dan kelgan ba'zi sarlavhalar xom ko'rinadi, masalan `A &gt;200 Wh kg−1 …`.
- Ba'zilarida LaTeX bor, masalan `$$\upmu \upmu \text{ b } …$$`.
- Admin jadvalida ko'rinadi. Katalogda ham shunday bo'lishi ehtimoli bor.

**Tuzatish yo'nalishi:**
- Ingest paytida sarlavhaga bir marta `html.unescape` qo'llansin.
- LaTeX'ni ko'rsatish yoki tozalash bo'yicha qaror kerak.

---

Shu ishga tushirishdagi boshqa natijalar issue emas: muhitga bog'liq.
- `test_data_quality_scan_regression` (4 ta) va `test_data_quality_duplicate_perf`
  staging ma'lumoti va korpusini talab qiladi.
- Bo'sh lokal bazada ular o'tmaydi. Staging tiklangach u yerda qayta tekshirilsin.
