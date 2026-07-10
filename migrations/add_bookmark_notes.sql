-- Saqlangan dissertatsiyalar ("Saqlangan") — mavjud user_bookmarks jadvalini
-- kengaytiradi (PARALLEL jadval EMAS: yagona manba — dashboard yulduzchasi va
-- /cabinet/saqlangan sahifasi bir xil jadvaldan o'qiydi).
--
-- Jadvalning o'zi odatda data.py:_ensure_dashboard_schema() tomonidan lazy
-- yaratiladi; bu migratsiya idempotent, shu sabab serverda qo'lda ham
-- xavfsiz ishga tushiriladi.

CREATE TABLE IF NOT EXISTS user_bookmarks (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  dissertation_id INTEGER NOT NULL REFERENCES dissertations(id) ON DELETE CASCADE,
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(user_id, dissertation_id)
);

-- yangi: foydalanuvchining shaxsiy eslatmasi (max 500 belgi ilova tomonda)
ALTER TABLE user_bookmarks ADD COLUMN IF NOT EXISTS notes TEXT;

CREATE INDEX IF NOT EXISTS idx_user_bookmarks_user
  ON user_bookmarks(user_id, created_at DESC);
