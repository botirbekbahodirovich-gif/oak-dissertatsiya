-- dissertations.oak_id: OAK e'lonining ID'si, importning dublikatga qarshi kaliti.
-- FAQAT QO'SHUVCHI. Prod va staging'da no-op: ustun, noyob constraint va indeks
-- allaqachon bor (2026-11-17 da prod'dan faqat o'qib tekshirildi: integer,
-- dissertations_oak_id_key UNIQUE, idx_dissertations_oak_id).
--
-- Nega kerak: ustun ilgari faqat qo'lda ishlatiladigan migrate_columns.py da
-- qo'shilardi. Bo'sh (yangi) bazada oak_id yo'q edi, shu sababli:
--   * app._run_startup_migrations() idx_dissertations_oak_id da yiqilib, butun
--     tranzaksiyani bekor qilardi;
--   * scraper.py, upload.py, data.py va scripts/daily_scraper.py dagi
--     INSERT ... ON CONFLICT (oak_id) noyob indekssiz ishlamaydi.
--
-- IF NOT EXISTS nom bo'yicha tekshiriladi: prod'dagi constraint indeksi aynan
-- dissertations_oak_id_key deb atalgan, shuning uchun u yerda hech narsa
-- yaratilmaydi. Tur prod bilan bir xil: INTEGER.
--
-- Rollback ataylab yo'q: prod'da bu ustun 026 dan ancha oldin bor va
-- o'chirilmasligi kerak.
BEGIN;
ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS oak_id INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS dissertations_oak_id_key ON dissertations (oak_id);
CREATE INDEX IF NOT EXISTS idx_dissertations_oak_id ON dissertations (oak_id);
COMMIT;
