# Rejalashtirilgan maqola metadata sinxronizatsiyasi (Phase 2C.3C)

Bu hujjat [`article-metadata-sync.md`](article-metadata-sync.md) (Phase
2C.3A — diff engine, safe/review/critical policy, stale himoya, apply
semantikasi) va
[`article-metadata-sync-dashboard.md`](article-metadata-sync-dashboard.md)
(Phase 2C.3B — markaziy review dashboard) ustiga quriladi va ularni **qayta
yozmaydi**.

**Bir jumlada:** cron/operator bazadagi DOI'li maqolalarni belgilangan
interval bo'yicha avtomatik qayta tekshiradi, mavjud `check_article_
metadata()` orqali — natijalar 2C.3B dashboard'ning review navbatiga tushadi.
**Scheduler faqat tekshiradi. Hech qanday maydonni avtomatik qo'llamaydi
(apply qilmaydi).**

```text
Cron / operator
      |
scripts/sync_article_metadata.py
      |
global scheduler lock (pg_advisory_lock)
      |
select due DOI articles (never-checked first, then oldest)
      |
bounded batch (ARTICLE_METADATA_SYNC_BATCH_SIZE)
      |
check_article_metadata(actor_type='system')   <- 2C.3A, QAYTA YOZILMAGAN
      |
article_metadata_syncs                        <- 2C.3A jadvali
      |
2C.3B Dashboard (/admin/articles/metadata-sync)
```

---

## 1. Nega CLI + cron (Celery/Redis emas)

Repository'da mavjud background-job standart — `scripts/daily_scraper.py`,
`scripts/grant_scraper.py`, `scripts/update_photo_urls.py`: oddiy, Flask'ga
bog'liq bo'lmagan skript, `DATABASE_URL` orqali to'g'ridan-to'g'ri
`psycopg2.connect()`, OS cron (`/etc/cron.d/...`, `deploy/install_cron.sh`)
tomonidan chaqiriladi. Loyihada Celery/Redis/APScheduler/systemd timer
YO'Q — shu uchun **aynan shu naqsh** takrorlanadi:
`scripts/sync_article_metadata.py`.

Flask process ichida doimiy background thread yoki Gunicorn worker ichida
scheduler ISHGA TUSHIRILMAYDI:

- bir nechta Gunicorn worker bo'lsa, har biri alohida schedule bajarishi
  mumkin edi (duplicate ishlar);
- worker restartlari (deploy, memory limit) scheduling'ni buzardi;
- web request process background scheduler uchun ishonchli source-of-truth
  emas.

Scheduler uchun alohida HTTP endpoint ham YARATILMAGAN (na admin, na public)
— CLI + cron yetarli va kichikroq xavfsizlik yuzasi (§41).

---

## 2. Fayllar

| Fayl | Vazifa |
| --- | --- |
| `scripts/sync_article_metadata.py` | CLI — argument parsing, DB ulanish, konfiguratsiya, chiqish kodi. Business logika YO'Q. |
| `services/article_sync/scheduler.py` | Orkestratsiya: lock, due tanlash, bounded batch, item-level isolation, run audit. `check_article_metadata()`ni CHAQIRADI, qayta yozmaydi. |
| `services/article_sync/scheduler_config.py` | `ARTICLE_METADATA_SYNC_*` env o'qish, validatsiya (§28 — noto'g'ri qiymat BALAND OVOZDA xato). |
| `services/article_sync/scheduler_state.py` | Run holati/trigger konstantalari — bitta joyda (route/CLI/docs bo'ylab qo'lda takrorlanmaydi). |
| `services/article_sync/scheduler_persistence.py` | DB qatlami: due-article SELECT, `article_metadata_sync_runs` CRUD. |
| `migrations/013_article_metadata_sync_scheduler.sql` | `article_metadata_sync_runs` jadvali (yangi, mustaqil — `article_metadata_syncs`ga tegilmaydi). |

Har bir maqolaning tekshiruv natijasi **hamon** `article_metadata_syncs`ga
yoziladi (2C.3A jadvali) — `article_metadata_sync_runs` faqat RUN darajasidagi
(bir cron chaqiruvi) audit, parallel metadata jadval emas.

---

## 3. Due-article tanlash (§7-§13)

Eligible bo'lish sharti — faqat ikkitasi:

```sql
doi IS NOT NULL AND doi <> ''
AND publication_status <> 'archived'
```

**Policy tanlovi:** `draft` maqolalar ham eligible (faqat `archived` chetga
chiqariladi). Sabab — mavjud `publication_status` uchtadan biri: `draft`,
`published`, `archived` (`utils/article_rules.py:PUBLICATION_STATUSES`).
`archived` — admin ongli ravishda "bu maqola endi faol emas" deb belgilagan
holat, shuning uchun tashqi tekshiruv resursi behuda sarflanmasin. `draft`
esa hali ko'rib chiqilayotgan, lekin baribir DOI'si haqiqiy bo'lishi mumkin
bo'lgan yozuv — uni ham tekshirib qo'yish keyinroq review paytida foydali
bo'ladi va vazifa qiladigan zarar yo'q (check hech narsani yozmaydi).

### Manba: `article_metadata_syncs.checked_at`, EMAS `metadata_last_synced_at`

Ikkita nomzod ustun bor edi, ular BIR XIL EMAS:

| Ustun | Nima anglatadi | Qachon yangilanadi |
| --- | --- | --- |
| `article_metadata_syncs.checked_at` (eng so'nggisi, har maqola uchun) | **Oxirgi URINILGAN tekshiruv** | Har `check_article_metadata()` chaqiruvida — o'zgarish topilsa ham, topilmasa ham, keyinroq apply qilinsa ham qilinmasa ham. |
| `scientific_articles.metadata_last_synced_at` | **Oxirgi MUVAFFAQIYATLI YOZUV** | Faqat admin (yoki kelajakda avtomatik) `apply_field_changes()` chaqirganda — ya'ni review'dan o'tib, maydon haqiqatan yozilganda. |

Due-selection **`article_metadata_syncs.checked_at`**dan foydalanadi. Agar
`metadata_last_synced_at` ishlatilganda, o'zgarish topilmagan (yoki topilib,
lekin hali review navbatida turgan) maqolalar HAR RUN'da qayta due bo'lib
qolardi — chunki ular hech qachon apply qilinmagan bo'lishi mumkin.

### Fair scheduling (§12-§13)

```sql
ORDER BY latest.last_checked_at ASC NULLS FIRST, a.id ASC
```

Hech qachon tekshirilmagan maqolalar (`NULLS FIRST`) birinchi, keyin eng
ESKI tekshirilgan. `a.id ASC` — deterministik tie-break (bir xil
`checked_at`da ham barqaror tartib). Shu tufayli 1000 maqola bo'lsa, doim
birinchi 50 tasi emas — navbat aylanadi, hech kim doim och qolmaydi.

---

## 4. Interval va batch (§9-§13, §25, §28)

```bash
ARTICLE_METADATA_SYNC_ENABLED=true
ARTICLE_METADATA_SYNC_INTERVAL_DAYS=30    # default: konservativ, o'zgartiriladi
ARTICLE_METADATA_SYNC_BATCH_SIZE=50       # default: bitta run'dagi maks. maqola
```

Hozircha **bitta** interval barcha maydon turlari uchun (bibliografik,
iqtibos, retraction) — MVP uchun yetarli. Multi-frequency engine (masalan
retraction tezroq, iqtibos sekinroq) hozircha YARATILMAGAN — kelajakda
`services/article_sync/scheduler_config.py`ga qo'shimcha maydon sifatida
kengaytirilishi mumkin, hozirgi arxitektura buni bloklamaydi (bitta
`interval_days`ni bir nechta interval bilan almashtirish yetarli bo'ladi).

Noto'g'ri qiymat (manfiy/nol/raqam bo'lmagan) — `SchedulerConfigError`,
CLI chiqish kodi 2 (jim defaultga QAYTMAYDI — bu `services/article_metadata/
config.py`dagi konvensiyadan ATAYIN farq, chunki noto'g'ri interval sukut
bo'yicha xavfli natijaga olib kelishi mumkin).

Operator `--limit N` bilan batch'ni bir martalik oshirishi/kamaytirishi
mumkin, lekin `SAFETY_MAX_BATCH_SIZE=500`dan oshib keta olmaydi (bir run
minglab maqolani birdan API'ga yubormasin).

---

## 5. Global lock — overlapping run'lardan himoya (§14, §23)

```sql
SELECT pg_try_advisory_lock(hashtext('article_metadata_scheduler'));
```

Session-scoped, non-blocking: ikkinchi run darhol `SchedulerAlreadyRunning
Error` oladi (cheksiz kutmaydi) va CLI `already_running` deb chiqadi (exit
0 — bu operatsion normal holat, xato emas). Lock connection yopilganda
Postgres tomonidan AVTOMATIK bo'shatiladi — process crash bo'lsa ham keyingi
run bloklanib qolmaydi.

`article_metadata_sync_runs.status` esa faqat KUZATUV — mutual exclusion
manbai LOCK, bu ustun emas. Shu sababli process kutilmagan tarzda o'lib
qolsa, run qatori abadiy `running` holida qolishi MUMKIN — bu alohida, kam
ta'sirli masala (keyingi run baribir ishlaydi, chunki lock allaqachon
bo'shagan). Kerak bo'lsa operator eski `running` qatorlarni qo'lda tekshirib,
`status`ni tozalashi mumkin — buni CLI o'zi hal qilmaydi.

## 6. Manual recheck bilan bir vaqtda (§15)

Admin panelda "qayta tekshirish" tugmasi va scheduler bir xil maqolani bir
vaqtda tekshirishi MUMKIN. Bu XAVFSIZ, chunki `check_article_metadata()`:

- `scientific_articles`/`article_authors`ga HECH NARSA yozmaydi (faqat
  `article_metadata_syncs`ga INSERT) — ikkita mustaqil o'qish + mustaqil
  INSERT, bir-biriga to'sqinlik qilmaydi;
- `apply_article_metadata_changes()` (haqiqiy yozuv) allaqachon
  `article_updated_at_snapshot` CAS-tekshiruvi bilan himoyalangan (2C.3A) —
  scheduler bu funksiyani UMUMAN chaqirmaydi.

Natija: ikkita deyarli bir xil sync yozuvi paydo bo'lishi mumkin — bu
ZARARSIZ (ikkalasi ham to'g'ri, mustaqil o'qishlar). Qo'shimcha
article-level lock/dedup QO'SHILMADI — kerak emas.

---

## 7. Item-level failure isolation va run hisoblari (§17, §22, §31)

Har maqola ALOHIDA transaction — `conn.commit()` har `check_article_
metadata()` chaqiruvidan keyin, xato bo'lsa `conn.rollback()`. Bitta
maqolaning timeout/xatosi navbatdagi 49 tasini TO'XTATMAYDI.

Invariant:

```text
total_selected == processed + skipped
processed       == success + partial + failed
```

| Hisob | Ma'nosi |
| --- | --- |
| `success` | Sync yaratildi, barcha provayder javob berdi (`status='ok'`). |
| `partial` | Sync yaratildi, kamida bitta provayder ishladi, kamida bittasi ishlamadi (§17 — "bitta provider failure butun run'ni yiqitmaydi"). |
| `failed` | Ikkita holat: (a) sync yaratildi, lekin BARCHA provayder ishlamadi (§18 — `check_article_metadata()`ning mavjud semantikasi qayta ishlatiladi, alohida error model YO'Q); (b) kutilmagan Python xatosi (masalan DB uzilishi) — sync yaratilmadi. |
| `skipped` | Tanlangandan keyin maqola ELIGIBLE bo'lmay qoldi (masalan admin DOI'ni olib tashladi) — `ArticleSyncError` (`not_found`/`missing_doi`). |

`ARTICLE_METADATA_SYNC_ENABLED=false` bo'lsa CLI hech narsa qilmay, exit 0
bilan chiqadi (run yaratmaydi).

---

## 8. Actor semantikasi (§37)

```python
check_article_metadata(cur, article_id, None, actor_type='system')
```

`actor_id=NULL`, `actor_type='system'` — soxta admin foydalanuvchi
YARATILMAYDI. Bu ustun allaqachon migration 012'da bor edi, aynan shu kun
uchun tayyorlangan (`services/article_sync/state.py:ACTOR_SYSTEM`).

---

## 9. Qat'iy TA'QIQLAR (§19-§21, Acceptance criteria)

Scheduler HECH QACHON:

- `apply_article_metadata_changes()`ni chaqirmaydi — hech qanday maydon
  (`title`, `citation_count`, `is_retracted`, ...) avtomatik yozilmaydi;
- retraction (`false -> true`) topsa ham maqolani o'zgartirmaydi — faqat
  `critical` severity bilan sync yaratadi, dashboard'ga tushadi, admin
  tasdiqlashi kerak;
- `access_type`/`license_type`/`pdf_path`ga tegmaydi, candidate PDF URL'ga
  so'rov yubormaydi, hech narsani yuklab olmaydi;
- yangi DOI/maqola YARATMAYDI — faqat MAVJUD, DOI'si bor maqolalarni
  tekshiradi;
- OAK klassifikatsiya yoki boshqa AI agentlik qilmaydi.

Bu qoidalar test bilan qulflangan
(`tests/test_article_metadata_scheduler_service.py::
NoAutomaticMutationTests`).

---

## 10. Xavfsizlik (§41)

- Public/admin HTTP trigger endpoint YO'Q — faqat CLI + cron.
- CLI'da arbitrary URL yo'q, DOI faqat mavjud provider qatlami
  (`services/article_metadata`) orqali.
- Log'da API kalit/email chiqmaydi (`services/article_metadata/config.py`
  konvensiyasi allaqachon shunga rioya qiladi) — scheduler qo'shimcha
  narsa log qilmaydi, faqat `run_id`, `article_id`, hisoblar.
- Raw SQL interpolatsiya yo'q — barcha so'rovlar parametrlangan.

---

## 11. Operatsion runbook

```bash
# 1. Qanday maqolalar due ekanini ko'rish (hech narsa yozmaydi/so'ramaydi)
python3.12 scripts/sync_article_metadata.py --dry-run

# 2. Qo'lda bitta kichik run (masalan deploy'dan keyin tekshirish uchun)
python3.12 scripts/sync_article_metadata.py --limit 5 --manual

# 3. Loglarni tekshirish
tail -f /var/log/article-metadata-sync.log

# 4. Dashboard'da natijani tekshirish
#    /admin/articles/metadata-sync — "Oxirgi avtomatik tekshiruv" kartasi +
#    yangi "needs_review"/"critical" qatorlar

# 5. Cron'ni yoqish (server, sudo)
sudo bash deploy/install_cron.sh
```

Cron misoli (`deploy/article-metadata-sync.cron`, `deploy/install_cron.sh`
tomonidan `/etc/cron.d/article-metadata-sync`ga o'rnatiladi):

```cron
# 02:15 UTC = 07:15 Toshkent (UTC+5). Server UTC ishlatadi — boshqa
# oak-scraper/grant-scraper/photo-urls cron'lari bilan bir xil taxmin.
15 2 * * * botirbek cd /opt/olimlar-uz && /opt/olimlar-uz/venv/bin/python scripts/sync_article_metadata.py >> /var/log/article-metadata-sync.log 2>&1
```

Cron jadvali FAQAT `deploy/*.cron` va `deploy/install_cron.sh`da — ilova
manba kodida (`scripts/sync_article_metadata.py`) hardcode QILINMAGAN.

---

## 12. Test qamrovi

- `tests/test_article_metadata_scheduler_config.py` — interval/batch
  validatsiyasi, `enabled` bool parsing, default qiymatlar.
- `tests/test_article_metadata_scheduler_migration.py` — migration 013
  statik xavfsizligi (`test_article_sync_migration.py` naqshi).
- `tests/test_article_metadata_scheduler_service.py` — lock (birinchi
  oladi, ikkinchisi `already_running`, release'dan keyin uchinchisi oladi),
  item-level isolation (10 due, biri xato — 9tasi davom etadi), dry-run
  (0 provider chaqiruvi, 0 sync/run yozuvi), actor semantikasi, run audit
  status o'tishlari, avtomatik mutatsiya YO'QLIGI (real fake DB store
  ustida — maqola maydonlari o'zgarmaydi).
- `tests/test_article_metadata_scheduler_dbgated.py` — haqiqiy Postgres
  ustida due-selection SQL (`NULLS FIRST`, interval, archived/DOI filtri) —
  `TEST_DATABASE_URL` sozlanmagan bo'lsa o'tkazib yuboriladi (staging'ga xos
  policy, boshqa `*_dbgated.py`lar bilan bir xil).
