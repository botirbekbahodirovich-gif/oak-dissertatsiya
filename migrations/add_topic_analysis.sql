-- AI mavzu tahdid tahlili — foydalanuvchi so'rovlari jurnali.
--
-- Rejalashtirilayotgan dissertatsiya mavzusi mavjud korpus (dissertations)
-- bilan solishtiriladi, natija Groq AI orqali tahlil qilinadi. Bu jadval
-- har bir tahlilni yozadi: kunlik limitni (3/kun) hisoblash va tarix uchun.
--
-- Idempotent — qayta ishga tushirish xavfsiz. Xuddi shu DDL blueprint'ning
-- lazy _ensure_schema'sida ham bor (server birinchi so'rovda o'zi migratsiya
-- qiladi). Qo'lda:  psql "$DATABASE_URL" -f migrations/add_topic_analysis.sql

CREATE TABLE IF NOT EXISTS topic_analysis_log (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  topic TEXT NOT NULL,
  result_summary TEXT,
  similar_count INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_topic_log_user
  ON topic_analysis_log(user_id);
CREATE INDEX IF NOT EXISTS idx_topic_log_time
  ON topic_analysis_log(created_at);
