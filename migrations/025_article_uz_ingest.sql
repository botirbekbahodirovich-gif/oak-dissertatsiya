-- O'zbekiston maqolalari ommaviy ingestion'i (OpenAlex UZ + core) uchun
-- FAQAT QO'SHUVCHI o'zgarishlar. Mavjud qatorlar o'qilmaydi va qayta
-- yozilmaydi; hech qanday ustun/jadval o'chirilmaydi.
--
-- 1) scientific_articles.ingest_evidence — avtomatik qarorlarning mashina
--    o'qiydigan dalili: litsenziya kelishuvi (qaysi manba nima bergani),
--    OAK yo'nalishi taklifi (OpenAlex subfield -> OAK shifri, high/low) va
--    PDF olish izi (URL, sana, sha256). Admin tahrirlash formasi bu ustunga
--    tegmaydi (ARTICLE_WRITE_COLUMNS da yo'q), shuning uchun dalil
--    yo'qolmaydi. Odam o'qiydigan izoh `permission_note` da.
-- 2) article_discovery_saved_queries.country_code / core_only — saqlangan
--    discovery so'rovida "Faqat O'zbekiston mualliflari" va "Faqat sifatli
--    (core) jurnallar" belgilari.
--
-- blueprints/articles.py:_ensure_schema va
-- services/article_discovery/saved_queries.py:ensure_schema bilan AYNAN
-- bir xil DDL (parity testlari).
--
-- Rollback (ATAYIN bajarilmaydigan izoh — ma'lumotni yo'q qiladi):
--   ALTER TABLE scientific_articles DROP COLUMN IF EXISTS ingest_evidence;
--   ALTER TABLE article_discovery_saved_queries DROP COLUMN IF EXISTS country_code;
--   ALTER TABLE article_discovery_saved_queries DROP COLUMN IF EXISTS core_only;
BEGIN;

ALTER TABLE scientific_articles
    ADD COLUMN IF NOT EXISTS ingest_evidence JSONB;

ALTER TABLE article_discovery_saved_queries
    ADD COLUMN IF NOT EXISTS country_code VARCHAR(2),
    ADD COLUMN IF NOT EXISTS core_only    BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
