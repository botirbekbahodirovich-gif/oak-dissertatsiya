-- Maqolaning rasmiy (asl) sarlavhasini o'zgartirmasdan, o'zbekcha ko'rinishini
-- alohida saqlash. NULL eski yozuvlar va tarjima qilinmagan maqolalar uchun
-- to'g'ri fallback hisoblanadi.
BEGIN;

ALTER TABLE scientific_articles
    ADD COLUMN IF NOT EXISTS title_uz TEXT;

COMMIT;
