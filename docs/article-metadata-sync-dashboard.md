# Markaziy metadata sinxronizatsiya dashboard (Phase 2C.3B)

Ko'p maqolada topilgan metadata o'zgarishlarini bitta markaziy admin sahifada
ko'rish, saralash va review qilish. Bu hujjat
[`article-metadata-sync.md`](article-metadata-sync.md) (Phase 2C.3A — diff
engine, safe/review/critical policy, stale himoya, apply semantikasi) ustiga
quriladi va uni **qayta yozmaydi**.

**Bir jumlada:** dashboard — ko'rib chiqish oynasi, yangi apply/fetch logika
emas. Barcha yozish amallari (`apply`, `check`) 2C.3A'dagi mavjud
`services.article_sync.service` funksiyalariga boradi.

---

## 1. Routes

| Route | Vazifa |
| --- | --- |
| `GET /admin/articles/metadata-sync` | Markaziy dashboard — eng so'nggi sync har maqola uchun |
| `GET /admin/articles/metadata-sync/<sync_id>` | Bitta sync natijasi — diff, provayder holati, amallar |
| `POST /admin/articles/metadata-sync/<sync_id>/dismiss` | "Ko'rib chiqildi, yopish" |
| `POST /admin/articles/metadata-sync/bulk-apply-safe` | Tanlangan XAVFSIZ o'zgarishlarni ommaviy qo'llash |
| `POST /admin/articles/<article_id>/metadata/recheck` | Yangi sync yaratadi (mavjud `check_article_metadata()`) |
| `GET /admin/articles/<article_id>/metadata/history` | Bitta maqolaning TO'LIQ tarixi, sahifalangan |
| `POST /admin/articles/<article_id>/metadata/apply` | **2C.3A'dan qayta ishlatiladi** — dashboard yangi apply yozmaydi |

Hammasi `blueprints/articles.py`da (yangi blueprint yaratilmadi — DOI
autofill/check/apply bilan bir joyda, bir xil avtorizatsiya konvensiyasi).

---

## 2. "Actionable sync" ta'rifi va latest-sync policy (§7, §21)

Bir maqola bir necha marta tekshirilgan bo'lishi mumkin. Dashboard **default
holatda faqat eng so'nggi sync**ni ko'rsatadi — butun tarixni emas.

```sql
WITH latest AS (
    SELECT DISTINCT ON (s.article_id) s.*, a.title, a.doi, …,
           (a.updated_at IS DISTINCT FROM s.article_updated_at_snapshot) AS is_stale
    FROM article_metadata_syncs s
    JOIN scientific_articles a ON a.id = s.article_id
    ORDER BY s.article_id, s.checked_at DESC, s.id DESC
)
```

`DISTINCT ON` — Postgres-only, loyihada yagona DB backend (SQLite yo'q,
qarang `docs/article-bulk-import.md`).

**Actionable** — `status='checked' AND NOT is_stale AND change_count > 0`
(ya'ni `needs_review` yoki `critical` queue holati). Bu — DEFAULT filtr.
Boshqa barcha filtrlar (`no_changes`, `stale`, `applied`, `dismissed`, `all`)
butun tarixni ko'radi.

**Duplicate active syncs muammosi** (§21 — admin bir maqolani 10 marta
tekshirsa): eng SODDA yechim tanlandi — hech qanday `superseded` status
YO'Q. `DISTINCT ON` allaqachon faqat ENG SO'NGGI qatorni tanlaydi, shuning
uchun eski `checked` (hali qo'llanmagan) synclar avtomatik ravishda default
queue'dan CHIQIB ketadi — ular faqat "Butun tarix" filtri yoki
`/metadata/history` orqali ko'rinadi. Tasodifiy apply xavfi yo'q, chunki
stale-himoya (2C.3A) baribir har qanday eski sync'ni bloklaydi, agar
u orqali to'g'ridan-to'g'ri (sync_id bilan) ochilsa ham.

---

## 3. Queue holati — yagona haqiqat manbai (§6)

`article_metadata_syncs.status` DB ustuni faqat 3 qiymat oladi:
`checked | applied | dismissed`. Boshqa hamma narsa —
`services/article_sync/state.py:derive_queue_state()` orqali DETERMINISTIK
chiqariladi (saqlanmaydi, qayta hisoblanadi):

```text
status='applied'                                    -> applied
status='dismissed'                                   -> dismissed
is_stale                                              -> stale
status='checked', critical_change_count>0             -> critical
status='checked', change_count=0                      -> no_changes
status='checked', boshqa hollarda                     -> needs_review
```

Route/shablon holat matnini HECH QACHON o'zi yaratmaydi — faqat
`QUEUE_STATE_LABELS`/`derive_queue_state()`dan o'qiydi. Dashboard qatorlari
`services.article_sync.service.list_dashboard()`da SERVER tomonida
`queue_state` bilan DEKORATSIYA qilinadi — shablon holatni qayta
HISOBLAMAYDI.

---

## 4. Actor semantikasi (§3, §29)

`article_metadata_syncs.actor_type` (`admin | system | agent`) +
`checked_by` (endi **NULLABLE**, migration 012). Hozircha faqat
`actor_type='admin'`, `checked_by=<admin.id>` yoziladi.

```python
check_article_metadata(cur, article_id, actor_id, actor_type='admin', ...)
```

Kelajakdagi scheduler (Phase 2C.3C) `actor_type='system', actor_id=None`
bilan AYNAN shu funksiyani chaqiradi — parallel logika yaratilmagan.
Soxta "system" foydalanuvchi ID YARATILMAGAN — `checked_by` shunchaki NULL
qoladi.

`services.article_sync.state.validate_actor()` — `admin` uchun `actor_id`
majburiy, `system`/`agent` uchun ixtiyoriy (odatda `None`).

---

## 5. Filtrlar va saralash (§8, §9)

Filtrlar (`services/article_sync/persistence.py:_dashboard_where`, BITTA
joyda):

```text
status  — actionable(default) | needs_review | critical | no_changes |
          stale | applied | dismissed | all
retraction=1     — has_retraction_change
citation_only=1  — has_citation_change AND change_count=1 ("faqat citation")
provider_issue=1 — has_provider_issue
publication_status — scientific_articles.publication_status
```

`has_retraction_change`/`has_citation_change`/`has_author_change`/
`has_provider_issue` — migration 012 bilan qo'shilgan denormalizatsiya
(INSERT vaqtida `diff_summary`/`provider_summary`dan hisoblanadi) — har
dashboard so'rovida JSONB'ni qayta tahlil qilmaslik uchun (§23 — real
query ehtiyoji, premature emas).

Saralash: `critical_first` (default), `newest`, `oldest`, `most_changes`.

---

## 6. Dismiss (§12)

`POST /admin/articles/metadata-sync/<sync_id>/dismiss` —
`article_metadata_syncs.status` `checked -> dismissed` (CAS: faqat
`checked` holatidan). `dismissed_at`/`dismissed_by`/`dismissed_reason`
saqlanadi.

**Dismiss ≠ reject article.** `scientific_articles`ga HECH NARSA
yozilmaydi — faqat shu SYNC candidate yopiladi. Tarix o'chirilmaydi,
`/metadata/history`da ko'rinishda qoladi.

---

## 7. Recheck (§20)

`POST /admin/articles/<id>/metadata/recheck` — mavjud
`check_article_metadata()`ni chaqiradi, YANGI sync yozuvi yaratadi.
Eski sync'lar (checked/applied/dismissed) **hech qachon overwrite
qilinmaydi** — tarix to'liq saqlanadi (`test_recheck_creates_new_sync_and_preserves_old`,
`test_recheck_does_not_overwrite_applied_history`).

---

## 8. Bulk-safe-apply (§16)

`POST /admin/articles/metadata-sync/bulk-apply-safe` — `sync_id[]`
qabul qiladi. Har biri uchun:

```text
queue_state == 'needs_review'   (critical/stale/applied/dismissed — YO'Q)
AND NOT has_retraction_change
AND NOT stale (mustaqil ravishda apply ichida ham tekshiriladi)
```

bo'lsagina, o'sha sync'ning SAFE + `default_selected` maydonlari
`apply_article_metadata_changes()` orqali qo'llaniladi — **yangi apply
logika YOZILMAGAN**, xuddi shu 2C.3A funksiyasi bir nechta sync uchun
ketma-ket chaqiriladi (`services/article_sync/service.py:bulk_apply_safe_changes`).
Bitta sync'ning muvaffaqiyatsizligi qolganlarini to'xtatmaydi
(`applied`/`skipped` xulosasi qaytadi).

Review/critical maydonlar bulk ro'yxatga **hech qachon** kirmaydi —
checkbox jadvalda faqat `queue_state == 'needs_review'` va
`NOT has_retraction_change` qatorlarda ko'rinadi.

---

## 9. Retraction va citation dashboard'da (§14, §15)

- Xulosa kartochkasi: "Muhim: N (shundan retraction: M)".
- Retraction — matn bilan "MUHIM" yorlig'i, emoji YO'Q, faqat rang bilan
  emas.
- "Faqat citation" filtri (`citation_only=1`) — sof citation-bump synclarni
  ajratib ko'rsatadi (safe-only navbat ko'payib ketmasligi uchun), lekin
  bulk-apply YANGI logika bilan avtomatlashtirilmagan — mavjud
  bulk-safe-apply umumiy mexanizmi orqali (§8) qamrab olinadi.

---

## 10. Tarix (§17)

`GET /admin/articles/<id>/metadata/history` — bitta maqolaning TO'LIQ
sinxronizatsiya tarixi, sahifalangan (20/sahifa). Xom API javobi YO'Q —
faqat saqlangan xulosa (checked_at, actor, o'zgarish soni, natija).

---

## 11. Xavfsizlik/performance (§22-§25)

- Barcha route — `_require_admin()` + global CSRF.
- `sync_id` -> `article_id` munosabati serverda tekshiriladi
  (`get_sync_result_row`, `apply_article_metadata_changes`) — client
  boshqa maqolaning sync'ini almashtira olmaydi.
- Dashboard — 2 so'rov (xulosa + sahifa), N+1 yo'q. Sahifalash — 25/sahifa
  (loyiha standarti).
- Yangi index — faqat real ehtiyoj: `idx_article_metadata_syncs_actor_type`,
  partial `idx_article_metadata_syncs_critical` (critical_change_count>0).

---

## 12. Testlar (§28-§32)

```text
services/article_sync/state.py              — pure unit (queue state, actor)
persistence._dashboard_where()               — pure unit (filtr SQL matni)
tests/test_article_sync_dashboard_service.py — dismiss/recheck/bulk-apply (qalbaki DB)
tests/test_article_sync_dashboard_routes.py  — auth/CSRF/side-effectsiz/oqim
tests/test_article_sync_dbgated.py           — DISTINCT ON/filtr — REAL Postgres
                                                (TEST_DATABASE_URL, staging)
```

`DISTINCT ON`/`FILTER (WHERE …)` haqiqiy semantikasi qalbaki DB'da ATAYIN
simulyatsiya qilinmagan (`tests/fake_article_sync_pg.py` docstringi) — bu
Postgres-ga xos konstruksiyalarni qayta yozib, soxta ishonch berardi.

---

## 13. Phase 2C.3C scheduler kontrakti (§36)

Hozir scheduler YO'Q. Tayyor bo'lishi kerak bo'lgan oqim:

```text
Scheduler
  ↓ due articles
  ↓ check_article_metadata(cur, article_id, actor_id=None, actor_type='system')
  ↓ article_metadata_syncs (checked_by=NULL, actor_type='system')
  ↓ Dashboard (actor_type filtri orqali ham ko'rinadi)
  ↓ safe/review/critical — AVTOMATIK APPLY YO'Q
  ↓ Admin ko'rib chiqadi/qo'llaydi/dismiss qiladi
```

Scheduler faqat **tekshiradi**. Review/critical avtomatik qo'llanmaydi —
bu qoida Phase 2C.3B'da HAM (bulk-safe-apply faqat safe+needs_review) buzilmaydi.
