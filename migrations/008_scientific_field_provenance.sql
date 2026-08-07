-- ============================================================================
-- Migration 008 — fan yo'nalishlari uchun manba (provenance) ustunlari
-- ============================================================================
-- OAK ixtisosliklari dataseti (289 ta yozuv) import qilinganda har bir
-- yo'nalish QAYERDAN kelgani kuzatilishi shart: qaysi fayl, qaysi qator,
-- fayl qaysi revizyada (SHA-256) edi, yozuv avtomatik o'tdimi yoki odam
-- tasdiqladimi. Busiz keyingi revizya bilan solishtirish va xato nomni
-- orqaga qaytarish imkoni bo'lmaydi.
--
-- Faqat `scientific_fields` ga NULLable ustunlar qo'shiladi. Mavjud qatorlar
-- o'qilmaydi va qayta yozilmaydi; hech qanday NOT NULL kiritilmaydi.
--
-- XAVFSIZLIK XUSUSIYATLARI (test_oak_import.py bilan tekshiriladi):
--   * Bitta tranzaksiya (BEGIN/COMMIT).
--   * Idempotent — ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--   * DROP, TRUNCATE, DELETE, UPDATE yo'q.
--   * blueprints/articles.py:_ensure_schema AYNAN shu ustunlarni qo'shadi.
--
-- USTUNLAR
--   raw_name            — manbadagi ASL matn, hech qachon tozalanmagan holda.
--                         Normalizatsiya yoki review xato bo'lsa, haqiqat
--                         manbai shu (parser hech qachon nom o'ylab topmaydi).
--   source_line         — manba faylidagi qator raqami (audit va review UI).
--   source_sha256       — manba faylining o'sha paytdagi to'liq hash'i.
--   review_status       — qanday tasdiqlangani:
--                           auto_clean        — texnik buzilishsiz
--                           auto_normalized   — faqat format tozalandi
--                           manual_approved   — odam ko'rib tasdiqladi
--                           seed              — repozitoriy seed'i (22 tarmoq)
--   reviewed_by/at      — kim va qachon tasdiqladi (users ga FK YO'Q —
--                         foydalanuvchilar legacy SQLite'da).
--   missing_parent_code — bola yozuvning HAQIQIY ota shifri, agar o'sha ota
--                         hali bazada bo'lmasa. Sun'iy ota yozuv YARATILMAYDI
--                         (rasmiy nomi yo'q); bola eng yaqin MAVJUD ajdodga
--                         (odatda fan tarmog'iga) bog'lanadi va UI bu ustun
--                         orqali "05.01 guruhi" degan KODNI ko'rsatadi,
--                         o'ylab topilgan nomni emas.
-- ============================================================================

BEGIN;

ALTER TABLE scientific_fields ADD COLUMN IF NOT EXISTS raw_name TEXT;
ALTER TABLE scientific_fields ADD COLUMN IF NOT EXISTS source_line INTEGER;
ALTER TABLE scientific_fields ADD COLUMN IF NOT EXISTS source_sha256 CHAR(64);
ALTER TABLE scientific_fields ADD COLUMN IF NOT EXISTS review_status VARCHAR(24);
ALTER TABLE scientific_fields ADD COLUMN IF NOT EXISTS reviewed_by INTEGER;
ALTER TABLE scientific_fields ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;
ALTER TABLE scientific_fields ADD COLUMN IF NOT EXISTS missing_parent_code VARCHAR(32);

-- Admin filtrlarining issiq yo'llari (§18): review holati va yetim guruh.
CREATE INDEX IF NOT EXISTS idx_scientific_fields_review
    ON scientific_fields (review_status);
CREATE INDEX IF NOT EXISTS idx_scientific_fields_missing_parent
    ON scientific_fields (missing_parent_code)
    WHERE missing_parent_code IS NOT NULL;
-- Katta taksonomiyada kod bo'yicha qidiruv va tartiblash (289+ yozuv).
CREATE INDEX IF NOT EXISTS idx_scientific_fields_code_sort
    ON scientific_fields (code);

COMMIT;

-- ============================================================================
-- ROLLBACK (qo'lda; ataylab izohda — u manba ma'lumotini yo'q qiladi):
--
--   BEGIN;
--   DROP INDEX IF EXISTS idx_scientific_fields_review;
--   DROP INDEX IF EXISTS idx_scientific_fields_missing_parent;
--   DROP INDEX IF EXISTS idx_scientific_fields_code_sort;
--   ALTER TABLE scientific_fields
--       DROP COLUMN IF EXISTS raw_name,
--       DROP COLUMN IF EXISTS source_line,
--       DROP COLUMN IF EXISTS source_sha256,
--       DROP COLUMN IF EXISTS review_status,
--       DROP COLUMN IF EXISTS reviewed_by,
--       DROP COLUMN IF EXISTS reviewed_at,
--       DROP COLUMN IF EXISTS missing_parent_code;
--   COMMIT;
--
-- Yo'nalish QATORLARI bu bilan o'chmaydi — faqat manba metadatasi yo'qoladi.
-- Import qilingan yo'nalishlarni butunlay olib tashlash uchun avval ularga
-- bog'langan maqolalarni ko'rib chiqish kerak (FK ON DELETE RESTRICT).
-- ============================================================================
