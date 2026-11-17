# Migratsiyalar raqamlari reestri

Raqamni band qilishdan oldin shu jadvalga qarang va yangi qatorni bir xil
commit'da qo'shing. Raqam boshqa branch'da band bo'lsa, fayl bu yerda hali
bo'lmasa ham QAYTA ISHLATILMAYDI.

| Raqam | Fayl | Branch / deploy | Holat |
|---|---|---|---|
| 022 | `022_dissertations_notified_at.sql` | fix/oak-api-scraper (prod e8f1c13e) | prod'da |
| 022 | `022_muassasa_recovered.sql` | feat/institution-backfill-v2 (wave1) | wave1 bilan |
| 023 | `023_olim_photos.sql` | feat/olim-photos (prod 2f660be0) | prod'da |
| 024 | `024_clear_supabase_photo_urls.sql` | feat/photo-url-cleanup | wave1 prod'ga chiqqach |
| 025 | `025_article_uz_ingest.sql` | feat/articles-uz | deploy-articles-uz |
| 026 | `026_dissertations_oak_id.sql` | feat/articles-uz | keyingi deploy (prod'da no-op) |

**Keyingi bo'sh raqam: 027.**

Eslatma: profil "ilmiy qiziqishlar" migratsiyasi (feat/home-profile-rail
keyingi bosqichi) uchun 026 emas, 027 dan boshlab bo'sh raqam olinsin.

`tests/test_article_discovery*_migration.py` dagi "gapsiz raqamlash" testi
022 va 024 ni band deb biladi — ular bu worktree'da bo'lmasligi mumkin.
Tarixiy `006` dublikati (ikki fayl) ataylab qoldirilgan.
